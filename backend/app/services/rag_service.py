"""Builds the grounded prompt and streams the answer."""
import logging
import re
from dataclasses import dataclass, field
from html import escape
from typing import AsyncIterator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.prompts import ANSWER_PROMPT_VERSION, ANSWER_SYSTEM_PROMPT
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
from app.services.retrieval_service import RetrievedChunk, document_chunks_in_order, hybrid_search, load_catalog

logger = logging.getLogger(__name__)

NO_SOURCES = "(no matching passages were found in the user's documents)"
_BLOCK_TAG_RE = re.compile(r"<(/?)(sources?|catalog|question)\b", re.IGNORECASE)
_CITED_RE = re.compile(r"\[(\d{1,3})\]")


def _neutralize(text: str) -> str:
    """Stops document text from closing or opening the prompt's structural tags."""
    return _BLOCK_TAG_RE.sub(lambda m: "‹" + m.group(1) + m.group(2), text)


def _attr(value: Optional[object]) -> str:
    return escape(str(value), quote=True) if value is not None else ""


def format_sources(chunks: List[RetrievedChunk]) -> str:
    if not chunks:
        return f"<sources>\n{NO_SOURCES}\n</sources>"
    parts = ["<sources>"]
    for index, chunk in enumerate(chunks, start=1):
        attrs = f'id="{index}" title="{_attr(chunk.filename)}" kind="{_attr(chunk.file_type)}"'
        if chunk.location:
            attrs += f' location="{_attr(chunk.location)}"'
        if chunk.modified_date:
            attrs += f' modified="{chunk.modified_date.date().isoformat()}"'
        parts.append(f"<source {attrs}>\n{_neutralize(chunk.content.strip())}\n</source>")
    parts.append("</sources>")
    return "\n".join(parts)


def format_catalog(catalog: List[CatalogEntry], limit: int = 200) -> str:
    lines = [f'<catalog count="{len(catalog)}">']
    for entry in catalog[:limit]:
        modified = f", modified {entry.modified_date.date().isoformat()}" if entry.modified_date else ""
        lines.append(f"- {_neutralize(entry.filename)} ({entry.file_type}{modified}, folder {_neutralize(entry.folder_path)})")
    if len(catalog) > limit:
        lines.append(f"- ... and {len(catalog) - limit} more")
    lines.append("</catalog>")
    return "\n".join(lines)


def build_messages(question: str, history: List[Dict[str, str]], chunks: List[RetrievedChunk],
                   catalog: Optional[List[CatalogEntry]] = None) -> List[Dict[str, str]]:
    messages = [{"role": "system", "content": ANSWER_SYSTEM_PROMPT}]
    for turn in history[-settings.CHAT_HISTORY_TURNS * 2:]:
        content = strip_citations(turn["content"]).strip()[:2000]
        if content:
            messages.append({"role": turn["role"], "content": content})
    blocks = [format_sources(chunks)]
    if catalog is not None:
        blocks.append(format_catalog(catalog))
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


@dataclass
class PreparedAnswer:
    plan: QueryPlan
    chunks: List[RetrievedChunk]
    messages: List[Dict[str, str]]
    citations: List[dict] = field(default_factory=list)


async def prepare(db: AsyncSession, user_id: str, question: str, history: List[Dict[str, str]]) -> PreparedAnswer:
    catalog = await load_catalog(db, user_id)
    plan = await plan_query(question, history, catalog)
    chunks: List[RetrievedChunk] = []
    include_catalog = None
    if plan.intent == LIST_DOCUMENTS:
        include_catalog = catalog
    elif plan.intent == SUMMARIZE and plan.target_document_ids:
        chunks = await document_chunks_in_order(db, user_id, plan.target_document_ids[0], settings.CONTEXT_MAX_CHARS)
    elif plan.intent != CONVERSATION and catalog:
        chunks = await hybrid_search(db, user_id, plan)
    chunks = _fit_budget(chunks, settings.CONTEXT_MAX_CHARS)
    messages = build_messages(question, history, chunks, include_catalog)
    return PreparedAnswer(plan=plan, chunks=chunks, messages=messages, citations=citation_payload(chunks))


async def stream_answer(prepared: PreparedAnswer) -> AsyncIterator[str]:
    async for token in get_llm_client().stream(prepared.messages):
        yield token


PROMPT_METADATA = {"prompt_version": ANSWER_PROMPT_VERSION}
