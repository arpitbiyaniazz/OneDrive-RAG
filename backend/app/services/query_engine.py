"""Query understanding: follow-up rewriting, intent, and document-name targeting."""
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional

from app.core.config import settings
from app.prompts import REWRITE_SYSTEM_PROMPT
from app.services.llm_service import get_llm_client

logger = logging.getLogger(__name__)

DOCUMENT_QUESTION = "document_question"
SUMMARIZE = "summarize_document"
LIST_DOCUMENTS = "list_documents"
CONVERSATION = "conversation"
INTENTS = {DOCUMENT_QUESTION, SUMMARIZE, LIST_DOCUMENTS, CONVERSATION}

_CITATION_RE = re.compile(r"\[\d+\]")
_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|hiya|good (morning|afternoon|evening)|thanks?( you)?|thank you|thx|ok(ay)?|cool|great|"
    r"who are you|what can you do|what do you do|how can you help( me)?|help)\b[\s!.?]*$",
    re.IGNORECASE,
)
_LIST_RE = re.compile(
    r"\b(what|which|list|show|name)\b.{0,40}\b(documents?|files?|docs)\b.{0,40}\b(do i have|have i|are there|"
    r"are (indexed|synced|available|connected|uploaded)|indexed|synced|available|in my (drive|knowledge base))\b"
    r"|^\s*(list|show)( me)?( all| my)? (documents|files|docs)\b|\bhow many (documents|files)\b",
    re.IGNORECASE,
)
_SUMMARY_RE = re.compile(r"\b(summari[sz]e|summary|overview|tl;?dr|key (points|takeaways)|main points|gist)\b", re.IGNORECASE)
_FILE_TYPE_PATTERNS = [
    ("pdf", re.compile(r"\bpdfs?\b", re.IGNORECASE)),
    ("xlsx", re.compile(r"\b(spreadsheets?|excel|xlsx|sheets?)\b", re.IGNORECASE)),
    ("pptx", re.compile(r"\b(presentations?|slides?|decks?|pptx|powerpoints?)\b", re.IGNORECASE)),
    ("docx", re.compile(r"\b(word (documents?|files?)|docx)\b", re.IGNORECASE)),
]
_TIME_CUE_RE = re.compile(r"\b(modified|updated|changed|edited|added|created|new)\b", re.IGNORECASE)


@dataclass
class CatalogEntry:
    id: str
    filename: str
    file_type: str
    folder_path: str
    modified_date: Optional[datetime]
    drive_type: str


@dataclass
class QueryPlan:
    query: str
    search_query: str
    intent: str = DOCUMENT_QUESTION
    target_document_ids: List[str] = field(default_factory=list)
    file_types: List[str] = field(default_factory=list)
    modified_after: Optional[datetime] = None
    rewritten: bool = False


def strip_citations(text: str) -> str:
    return _CITATION_RE.sub("", text)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _stem(filename: str) -> str:
    base = filename.rsplit(".", 1)[0] if re.search(r"\.[A-Za-z0-9]{1,6}$", filename) else filename
    return _normalize(base)


def match_documents(query: str, catalog: List[CatalogEntry]) -> List[str]:
    """Documents the query names explicitly (whole-word match of the file name without extension)."""
    q = f" {_normalize(query)} "
    hits = []
    for entry in catalog:
        stem = _stem(entry.filename)
        if len(stem) >= 4 and f" {stem} " in q:
            hits.append((len(stem), entry.id))
    hits.sort(reverse=True)
    return [doc_id for _, doc_id in hits[:3]]


def _file_types(query: str) -> List[str]:
    return [t for t, pattern in _FILE_TYPE_PATTERNS if pattern.search(query)]


def _modified_after(query: str) -> Optional[datetime]:
    if not _TIME_CUE_RE.search(query):
        return None
    q = query.lower()
    now = datetime.now(UTC)
    if "today" in q:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "this week" in q or "last week" in q or "past week" in q:
        return now - timedelta(days=7)
    if "this month" in q:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if "last month" in q or "recent" in q or "lately" in q or "last 30 days" in q:
        return now - timedelta(days=30)
    if "this year" in q:
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def _rule_intent(query: str, targets: List[str]) -> str:
    if len(query) <= 80 and _GREETING_RE.match(query):
        return CONVERSATION
    if _LIST_RE.search(query):
        return LIST_DOCUMENTS
    if targets and _SUMMARY_RE.search(query):
        return SUMMARIZE
    return DOCUMENT_QUESTION


def _history_text(history: List[Dict[str, str]]) -> str:
    lines = []
    for turn in history[-settings.CHAT_HISTORY_TURNS * 2:]:
        content = " ".join(strip_citations(turn["content"]).split())[:600]
        lines.append(f"{turn['role']}: {content}")
    return "\n".join(lines)


async def _llm_rewrite(query: str, history: List[Dict[str, str]], catalog: List[CatalogEntry]) -> Optional[dict]:
    titles = "\n".join(f"- {e.filename}" for e in catalog[:200]) or "(none)"
    user = (
        f"Document list:\n{titles}\n\nConversation:\n{_history_text(history)}\n\n"
        f"Latest message:\n{query}\n\nReturn the JSON object."
    )
    try:
        raw = await get_llm_client().complete_json(
            [{"role": "system", "content": REWRITE_SYSTEM_PROMPT}, {"role": "user", "content": user}],
            request_timeout=settings.QUERY_REWRITE_TIMEOUT_SECONDS,
        )
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("Query rewrite failed, using the original question: %s", type(exc).__name__)
        return None


async def plan_query(query: str, history: List[Dict[str, str]], catalog: List[CatalogEntry]) -> QueryPlan:
    query = query.strip()
    targets = match_documents(query, catalog)
    plan = QueryPlan(
        query=query,
        search_query=query,
        intent=_rule_intent(query, targets),
        target_document_ids=targets,
        file_types=_file_types(query),
        modified_after=_modified_after(query),
    )
    # Self-contained first questions skip the extra LLM round trip.
    if not history or plan.intent == CONVERSATION:
        return plan

    data = await _llm_rewrite(query, history, catalog)
    if not data:
        return plan
    standalone = data.get("standalone_query")
    if isinstance(standalone, str) and 0 < len(standalone.strip()) <= 1000:
        plan.search_query = standalone.strip()
        plan.rewritten = plan.search_query != query
    intent = data.get("intent")
    if intent in INTENTS:
        plan.intent = intent
    by_title = {e.filename.lower(): e.id for e in catalog}
    named = [by_title[t.lower()] for t in data.get("target_documents") or [] if isinstance(t, str) and t.lower() in by_title]
    if named:
        plan.target_document_ids = list(dict.fromkeys(named + plan.target_document_ids))[:3]
    else:
        plan.target_document_ids = match_documents(plan.search_query, catalog) or plan.target_document_ids
    if plan.intent == SUMMARIZE and not plan.target_document_ids:
        plan.intent = DOCUMENT_QUESTION
    plan.file_types = plan.file_types or _file_types(plan.search_query)
    plan.modified_after = plan.modified_after or _modified_after(plan.search_query)
    return plan
