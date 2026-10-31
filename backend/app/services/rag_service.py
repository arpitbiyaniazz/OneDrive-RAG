"""Builds the grounded prompt and streams the answer."""
import asyncio
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, tzinfo
from html import escape
from typing import AsyncIterator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.prompts import (
    ANSWER_PROMPT_VERSION,
    ANSWER_REMINDER,
    ANSWER_SYSTEM_PROMPT,
    SUMMARY_PART_PROMPT,
    SUMMARY_PART_PROMPT_VERSION,
)
from app.services.answer_guard import scrub_passage
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
    documents_about,
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


_DRIVE_NAMES = {"onedrive": "OneDrive", "google_drive": "Google Drive"}
CATALOG_SHOWN = 200  # catalog entries listed in the prompt (the count is always exact)


def _local_day(value: Optional[datetime], tz: Optional[tzinfo]) -> Optional[str]:
    return value.astimezone(tz or UTC).date().isoformat() if value else None


def format_sources(chunks: List[RetrievedChunk], start: int = 1, tz: Optional[tzinfo] = None) -> str:
    """Passages as the model reads them: scrubbed of markup and instructions aimed at the assistant. When
    they come from different days, each is marked newest/older (the model misjudges ISO dates)."""
    if not chunks:
        return f"<sources>\n{NO_SOURCES}\n</sources>"
    days = {_local_day(c.modified_date, tz) for c in chunks if c.modified_date}
    newest = max(days) if len(days) > 1 else None
    parts = ["<sources>"]
    for index, chunk in enumerate(chunks, start=start):
        attrs = f'id="{index}" title="{_attr(chunk.filename)}" kind="{_attr(chunk.file_type)}"'
        if chunk.location:
            attrs += f' location="{_attr(chunk.location)}"'
        if chunk.modified_date:
            day = _local_day(chunk.modified_date, tz)
            attrs += f' modified="{day}"' + (f' recency="{"newest" if day == newest else "older"}"' if newest else "")
        if "mentions" in chunk.signals:
            attrs += f' mentions="{"yes" if chunk.signals["mentions"] else "no"}"'
        parts.append(f"<source {attrs}>\n{_neutralize(scrub_passage(chunk.content.strip()))}\n</source>")
    parts.append("</sources>")
    return "\n".join(parts)


def format_catalog(catalog: List[CatalogEntry], limit: int = CATALOG_SHOWN, total: Optional[int] = None,
                   tz: Optional[tzinfo] = None, filter_text: Optional[str] = None) -> str:
    """Newest documents first, numbered like sources so each listed document can be cited (linked).
    `total` is the exact count when the catalog was truncated; `filter_text` says which documents it holds."""
    total = max(total or 0, len(catalog))
    attrs = f'count="{total}"' + (f' filter="{_attr(filter_text)}"' if filter_text else "")
    lines = [f"<catalog {attrs}>"]
    for number, entry in enumerate(catalog[:limit], start=1):
        details = [entry.file_type]
        if entry.modified_date:
            details.append(f"modified {_local_day(entry.modified_date, tz)}")
        if entry.created_date and _local_day(entry.created_date, tz) != _local_day(entry.modified_date, tz):
            details.append(f"added {_local_day(entry.created_date, tz)}")
        details += [f"folder {_neutralize(entry.folder_path)}", _DRIVE_NAMES.get(entry.drive_type, entry.drive_type)]
        lines.append(f"[{number}] {_neutralize(entry.filename)} ({', '.join(details)})")
    if total > limit:
        lines.append(f"... and {total - min(limit, len(catalog))} more")
    lines.append("</catalog>")
    return "\n".join(lines)


def filter_description(plan: QueryPlan) -> Optional[str]:
    """The conditions of a document-list question in plain words, e.g. "file types pdf, in folder Finance"."""
    return ", ".join(filter(None, [
        f"file types {', '.join(plan.file_types)}" if plan.file_types else None,
        plan.date_range.describe() if plan.date_range else None,
        f"in folder {plan.folder}" if plan.folder else None,
    ])) or None


def format_inventory(plan: QueryPlan, documents: int, more: bool) -> str:
    """Tells the model the sources are one passage per candidate document for a document-list question."""
    attrs = f'topic="{_attr(plan.topic)}" documents="{documents}" more="{str(more).lower()}"'
    if plan.topic_mode == "mentions":
        attrs += ' match="mentions"'
    description = filter_description(plan)
    if description:
        attrs += f' filter="{_attr(description)}"'
    return f"<inventory {attrs}/>"


def excerpt(text: str, topic: str, max_chars: int) -> str:
    """At most `max_chars` of a passage, around the place it mentions the topic: the whole phrase if it
    occurs, otherwise its most distinctive (longest) word, so "parental leave" is not cut at "leave"."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    lower = text.lower()
    phrase = re.search(re.escape(" ".join(topic.lower().split())), lower)
    words = sorted(set(re.findall(r"\w{2,}", topic.lower())), key=len, reverse=True)
    hits = [phrase.start()] if phrase else [m.start() for word in words[:1]
                                            if (m := re.search(rf"\b{re.escape(word)}\b", lower))]
    start = max(0, min(hits) - max_chars // 3) if hits else 0
    end = min(len(text), start + max_chars)
    return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")


def format_notes(title: str, notes: List[str]) -> str:
    parts = [f'<notes title="{_attr(title)}" parts="{len(notes)}">']
    for number, text in enumerate(notes, start=1):
        parts.append(f'<part n="{number}">\n{_neutralize(scrub_passage(text.strip())) or "(no notes)"}\n</part>')
    parts.append("</notes>")
    return "\n".join(parts)


def system_prompt(today: Optional[str] = None) -> str:
    return f"{ANSWER_SYSTEM_PROMPT}\n\nToday's date: {today or datetime.now(UTC).date().isoformat()}."


def build_messages(question: str, history: List[Dict[str, str]], chunks: List[RetrievedChunk],
                   catalog: Optional[List[CatalogEntry]] = None, notes: Optional[str] = None,
                   catalog_total: Optional[int] = None, *, catalog_filter: Optional[str] = None,
                   inventory: Optional[str] = None, today: Optional[str] = None,
                   tz: Optional[tzinfo] = None) -> List[Dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt(today)}]
    for turn in history[-settings.CHAT_HISTORY_TURNS * 2:]:
        content = strip_citations(turn["content"]).strip()[:2000]
        if content:
            messages.append({"role": turn["role"], "content": content})
    if notes is not None:
        blocks = [notes]
    elif catalog is not None and not chunks:
        blocks = []  # a document list: the numbered catalog is the only source
    else:
        blocks = [format_sources(chunks, tz=tz)]
    if catalog is not None:
        blocks.append(format_catalog(catalog, total=catalog_total, tz=tz, filter_text=catalog_filter))
    if inventory:
        blocks.append(inventory)
    blocks.append(f"<question>\n{_neutralize(question)}\n</question>")
    if chunks and notes is None and catalog is None and not inventory:
        blocks.append(ANSWER_REMINDER)
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


def catalog_citations(entries: List[CatalogEntry]) -> List[dict]:
    """Citations for a numbered catalog: [n] in a document list links to the n-th document."""
    return [
        {
            "source_id": index,
            "document_id": e.id,
            "filename": e.filename,
            "file_type": e.file_type,
            "drive_type": e.drive_type,
            "folder_path": e.folder_path,
            "location": None,
            "page": None,
            "section": None,
            "url": e.url or None,
        }
        for index, e in enumerate(entries, start=1)
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
    today: Optional[str] = None  # the user's local date


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


async def _document_list(db: AsyncSession, user_id: str, plan: QueryPlan, question: str,
                         history: List[Dict[str, str]], catalog: List[CatalogEntry], today: str,
                         tz: Optional[tzinfo]) -> PreparedAnswer:
    """'Which/how many documents ...': with a topic, one passage per candidate document for the model to
    judge; otherwise the exact (date/type-filtered) catalog. Either way each document can be cited."""
    if plan.topic:
        found, more = await documents_about(db, user_id, plan, settings.INVENTORY_MAX_DOCUMENTS)
        chunks = [replace(c, content=excerpt(c.content, plan.topic, settings.INVENTORY_EXCERPT_CHARS)) for c in found]
        messages = build_messages(question, history, chunks, inventory=format_inventory(plan, len(chunks), more),
                                  today=today, tz=tz)
        return PreparedAnswer(plan=plan, chunks=chunks, messages=messages, citations=citation_payload(chunks),
                              question=question, history=history, today=today)
    filtered = bool(plan.file_types or plan.date_range or plan.folder)
    entries = (await load_catalog(db, user_id, CATALOG_SHOWN, plan.file_types, plan.date_range, plan.folder)
               if filtered else catalog)
    if filtered or len(catalog) >= CATALOG_LIMIT:
        total = await count_indexed(db, user_id, plan.file_types, plan.date_range, plan.folder)
    else:
        total = len(catalog)
    messages = build_messages(question, history, [], entries, catalog_total=total, catalog_filter=filter_description(plan),
                              today=today, tz=tz)
    return PreparedAnswer(plan=plan, chunks=[], messages=messages, citations=catalog_citations(entries[:CATALOG_SHOWN]),
                          question=question, history=history, today=today)


async def prepare(db: AsyncSession, user_id: str, question: str, history: List[Dict[str, str]],
                  tz: Optional[tzinfo] = None) -> PreparedAnswer:
    """`tz` is the user's time zone: "today", dates in answers and date filters are in their local time."""
    now = datetime.now(tz or UTC)
    today = now.date().isoformat()
    catalog = await load_catalog(db, user_id, CATALOG_LIMIT)
    plan = await plan_query(question, history, catalog, now=now)
    if plan.intent == LIST_DOCUMENTS:
        return await _document_list(db, user_id, plan, question, history, catalog, today, tz)
    chunks: List[RetrievedChunk] = []
    parts: List[List[int]] = []
    if plan.intent == SUMMARIZE and plan.target_document_ids:
        chunks, parts = await _summary_chunks(db, user_id, plan.target_document_ids)
    elif plan.intent != CONVERSATION and catalog:
        chunks = await hybrid_search(db, user_id, plan)
    if not parts:
        chunks = _fit_budget(chunks, settings.SUMMARY_MAX_CHARS if plan.intent == SUMMARIZE else settings.CONTEXT_MAX_CHARS)
    messages = [] if parts else build_messages(question, history, chunks, today=today, tz=tz)
    return PreparedAnswer(plan=plan, chunks=chunks, messages=messages, citations=citation_payload(chunks),
                          question=question, history=history, parts=parts, today=today)


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
        messages = build_messages(prepared.question, prepared.history, [], notes=format_notes(title, notes),
                                  today=prepared.today)
    async for token in get_llm_client().stream(messages):
        yield token


PROMPT_METADATA = {"prompt_version": ANSWER_PROMPT_VERSION, "summary_prompt_version": SUMMARY_PART_PROMPT_VERSION}
