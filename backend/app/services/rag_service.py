"""Builds the grounded prompt and streams the answer."""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from typing import AsyncIterator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.prompts import ANSWER_PROMPT_VERSION, ANSWER_SYSTEM_PROMPT, SUMMARY_PART_PROMPT, SUMMARY_PART_PROMPT_VERSION
from app.services.llm_service import get_llm_client
from app.services.query_engine import (
    CONVERSATION,
    LIST_DOCUMENTS,
    SUMMARIZE,
    CatalogEntry,
    QueryPlan,
    plan_query,
    strip_citations,
)
from app.services.retrieval_service import (
    RetrievedChunk,
    count_indexed,
    document_chunks_in_order,
    hybrid_search,
    load_catalog,
    sample_evenly,
)

logger = logging.getLogger(__name__)

NO_SOURCES = "(no matching passages were found in the user's documents)"
_BLOCK_TAG_RE = re.compile(r"<(/?)(sources?|catalog|question|notes?|part)\b", re.IGNORECASE)
_CITED_RE = re.compile(r"\[(\d{1,3})\]")
_PART_NOTES_MAX_TOKENS = 700
_PARALLEL_PARTS = 3


def _neutralize(text: str) -> str:
    """Stops document text from closing or opening the prompt's structural tags."""
    return _BLOCK_TAG_RE.sub(lambda m: "‹" + m.group(1) + m.group(2), text)


def _attr(value: Optional[object]) -> str:
    return escape(str(value), quote=True) if value is not None else ""


def format_sources(chunks: List[RetrievedChunk], start: int = 1) -> str:
    if not chunks:
        return f"<sources>\n{NO_SOURCES}\n</sources>"
    parts = ["<sources>"]
    for index, chunk in enumerate(chunks, start=start):
        attrs = f'id="{index}" title="{_attr(chunk.filename)}" kind="{_attr(chunk.file_type)}"'
        if chunk.location:
            attrs += f' location="{_attr(chunk.location)}"'
        if chunk.modified_date:
            attrs += f' modified="{chunk.modified_date.date().isoformat()}"'
        parts.append(f"<source {attrs}>\n{_neutralize(chunk.content.strip())}\n</source>")
    parts.append("</sources>")
    return "\n".join(parts)


def format_catalog(catalog: List[CatalogEntry], limit: int = 200, total: Optional[int] = None) -> str:
    """Newest documents first; `total` is the exact count when the catalog was truncated."""
    total = max(total or 0, len(catalog))
    lines = [f'<catalog count="{total}">']
    for entry in catalog[:limit]:
        modified = f", modified {entry.modified_date.date().isoformat()}" if entry.modified_date else ""
        lines.append(f"- {_neutralize(entry.filename)} ({entry.file_type}{modified}, folder {_neutralize(entry.folder_path)})")
    if total > limit:
        lines.append(f"- ... and {total - min(limit, len(catalog))} more")
    lines.append("</catalog>")
    return "\n".join(lines)


def format_notes(title: str, notes: List[str]) -> str:
    parts = [f'<notes title="{_attr(title)}" parts="{len(notes)}">']
    for number, text in enumerate(notes, start=1):
        parts.append(f'<part n="{number}">\n{_neutralize(text.strip()) or "(no notes)"}\n</part>')
    parts.append("</notes>")
    return "\n".join(parts)


def system_prompt(today: Optional[str] = None) -> str:
    return f"{ANSWER_SYSTEM_PROMPT}\n\nToday's date: {today or datetime.now(UTC).date().isoformat()}."


def build_messages(question: str, history: List[Dict[str, str]], chunks: List[RetrievedChunk],
                   catalog: Optional[List[CatalogEntry]] = None, notes: Optional[str] = None,
                   catalog_total: Optional[int] = None) -> List[Dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt()}]
    for turn in history[-settings.CHAT_HISTORY_TURNS * 2:]:
        content = strip_citations(turn["content"]).strip()[:2000]
        if content:
            messages.append({"role": turn["role"], "content": content})
    blocks = [notes] if notes is not None else [format_sources(chunks)]
    if catalog is not None:
        blocks.append(format_catalog(catalog, total=catalog_total))
    blocks.append(f"<question>\n{_neutralize(question)}\n</question>")
    messages.append({"role": "user", "content": "\n\n".join(blocks)})
    return messages


def citation_payload(chunks: List[RetrievedChunk]) -> List[dict]:
    return [
        {
            "source_id": index,
            "document_id": c.document_id,
            "filename": c.filename,
            "file_type": c.file_type,
            "drive_type": c.drive_type,
            "folder_path": c.folder_path,
            "location": c.location,
            "page": c.page_start,
            "section": c.section,
            "url": c.url or None,  # never fabricated: absent when the provider gave no link
        }
        for index, c in enumerate(chunks, start=1)
    ]


def cited_sources(answer: str, citations: List[dict]) -> List[dict]:
    used = {int(n) for n in _CITED_RE.findall(answer)}
    return [c for c in citations if c["source_id"] in used]


def _fit_budget(chunks: List[RetrievedChunk], max_chars: int) -> List[RetrievedChunk]:
    out, total = [], 0
    for chunk in chunks:
        if out and total + len(chunk.content) > max_chars:
            break
        out.append(chunk)
        total += len(chunk.content)
    return out


def _split_parts(chunks: List[RetrievedChunk], max_chars: int) -> List[List[int]]:
    """Consecutive groups of chunk indexes, each at most `max_chars` of text."""
    parts: List[List[int]] = [[]]
    size = 0
    for index, chunk in enumerate(chunks):
        if parts[-1] and size + len(chunk.content) > max_chars:
            parts.append([])
            size = 0
        parts[-1].append(index)
        size += len(chunk.content)
    return [p for p in parts if p]


@dataclass
class PreparedAnswer:
    plan: QueryPlan
    chunks: List[RetrievedChunk]
    messages: List[Dict[str, str]]
    citations: List[dict] = field(default_factory=list)
    question: str = ""
    history: List[Dict[str, str]] = field(default_factory=list)
    # Long single-document summaries: chunk indexes per part, summarized before the final answer.
    parts: List[List[int]] = field(default_factory=list)


async def _summary_chunks(db: AsyncSession, user_id: str, document_ids: List[str]) -> tuple[List[RetrievedChunk], List[List[int]]]:
    """Every passage of the named document(s), in reading order.

    One document that does not fit SUMMARY_MAX_CHARS is split into parts (summarized one by one);
    beyond SUMMARY_MAX_PARTS parts it is sampled evenly. Several documents share the budget.
    """
    budget = settings.SUMMARY_MAX_CHARS
    if len(document_ids) > 1:
        share = budget // len(document_ids)
        chunks: List[RetrievedChunk] = []
        for doc_id in document_ids:
            chunks += await document_chunks_in_order(db, user_id, doc_id, share)
        return chunks, []
    chunks = await document_chunks_in_order(db, user_id, document_ids[0])
    if sum(len(c.content) for c in chunks) <= budget:
        return chunks, []
    chunks = sample_evenly(chunks, budget * max(1, settings.SUMMARY_MAX_PARTS))
    return chunks, _split_parts(chunks, budget)


CATALOG_LIMIT = 5000


async def prepare(db: AsyncSession, user_id: str, question: str, history: List[Dict[str, str]]) -> PreparedAnswer:
    catalog = await load_catalog(db, user_id, CATALOG_LIMIT)
    plan = await plan_query(question, history, catalog)
    chunks: List[RetrievedChunk] = []
    parts: List[List[int]] = []
    include_catalog = None
    catalog_total = None
    if plan.intent == LIST_DOCUMENTS:
        include_catalog = [e for e in catalog if e.file_type in plan.file_types] if plan.file_types else catalog
        truncated = len(catalog) >= CATALOG_LIMIT and not plan.file_types
        catalog_total = await count_indexed(db, user_id) if truncated else len(include_catalog)
    elif plan.intent == SUMMARIZE and plan.target_document_ids:
        chunks, parts = await _summary_chunks(db, user_id, plan.target_document_ids)
    elif plan.intent != CONVERSATION and catalog:
        chunks = await hybrid_search(db, user_id, plan)
    if not parts:
        chunks = _fit_budget(chunks, settings.SUMMARY_MAX_CHARS if plan.intent == SUMMARIZE else settings.CONTEXT_MAX_CHARS)
    messages = [] if parts else build_messages(question, history, chunks, include_catalog, catalog_total=catalog_total)
    return PreparedAnswer(plan=plan, chunks=chunks, messages=messages, citations=citation_payload(chunks),
                          question=question, history=history, parts=parts)


async def _part_notes(prepared: PreparedAnswer) -> List[str]:
    """Notes for each part of a long document (bounded parallelism), with global source numbers."""
    llm = get_llm_client()
    gate = asyncio.Semaphore(_PARALLEL_PARTS)
    title = prepared.chunks[0].filename if prepared.chunks else ""

    async def one(number: int, indexes: List[int]) -> str:
        system = SUMMARY_PART_PROMPT.format(title=title.replace('"', "'"), part=number, parts=len(prepared.parts))
        sources = format_sources([prepared.chunks[i] for i in indexes], start=indexes[0] + 1)
        async with gate:
            return await llm.complete([{"role": "system", "content": system}, {"role": "user", "content": sources}],
                                      max_tokens=_PART_NOTES_MAX_TOKENS, request_timeout=settings.LLM_TIMEOUT_SECONDS)

    return list(await asyncio.gather(*(one(n, idx) for n, idx in enumerate(prepared.parts, start=1))))


async def stream_answer(prepared: PreparedAnswer) -> AsyncIterator[str]:
    messages = prepared.messages
    if prepared.parts:
        notes = await _part_notes(prepared)
        title = prepared.chunks[0].filename if prepared.chunks else ""
        messages = build_messages(prepared.question, prepared.history, [], notes=format_notes(title, notes))
    async for token in get_llm_client().stream(messages):
        yield token


PROMPT_METADATA = {"prompt_version": ANSWER_PROMPT_VERSION, "summary_prompt_version": SUMMARY_PART_PROMPT_VERSION}
