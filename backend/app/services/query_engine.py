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
_LIST_NOUNS = r"(documents?|files?|docs|spreadsheets?|workbooks?|presentations?|decks|pdfs?|pdf files|word (documents|files))"
_LIST_RE = re.compile(
    rf"\b(what|which|list|show|name)\b.{{0,40}}\b{_LIST_NOUNS}\b.{{0,40}}\b(do i have|have i|are there|"
    r"are (indexed|synced|available|connected|uploaded)|indexed|synced|available|in my (drive|knowledge base))\b"
    rf"|^\s*(list|show)( me)?( all)?( my)?( latest| recent| newest| indexed| synced)? {_LIST_NOUNS}\b"
    rf"|\bhow many {_LIST_NOUNS}\b",
    re.IGNORECASE,
)
# "How many documents must be submitted...", "which files do I have about pricing": content questions.
_NOT_INVENTORY_RE = re.compile(
    r"\b(must|should|need(ed)?|required|necessary|submit(ted)?|bring)\b"
    r"|\b(about|regarding|concerning|related to|that (mention|cover|discuss)|mentioning)\b",
    re.IGNORECASE,
)
_SUMMARY_RE = re.compile(
    r"\b(summari[sz]e|summary|overview|tl;?dr|recap|key (points|takeaways)|main points|gist|contents of|walk me through)\b"
    r"|\bwhat('s| is| are)\b.{1,80}\babout\s*\??\s*$",
    re.IGNORECASE,
)
_CONTENTS_RE = re.compile(r"\bwhat('s| is)\s+(in|inside)\b", re.IGNORECASE)
_RECENT_DOC_RE = re.compile(
    r"\b(latest|most recent|newest|last|recently (added|modified|updated|synced))\s+"
    r"((uploaded|added|modified|updated|synced|indexed)\s+)?(document|file|doc)\b",
    re.IGNORECASE,
)
# Words a pure summary request may contain besides the document's name.
_SUMMARY_FILLER = set(
    "the a an my our this that these those of for in on to and or please me give can you could would will i we it "
    "its is are be what whats s about tell show short brief briefly quick quickly full whole entire complete "
    "document documents file files doc docs inside latest most recent recently newest last uploaded added "
    "modified updated synced indexed summary summarize summarise overview gist tldr tl dr recap key main points "
    "takeaways contents walk through explain describe outline highlights pdf docx doc xlsx xls xlsm csv pptx ppt "
    "txt md html".split()
)
_SHEET_COMPOUND_RE = re.compile(r"\b(balance|term|fact|data|spec|time|cheat|style|score|call|tally)\s+sheets?\b", re.IGNORECASE)
_FILE_TYPE_PATTERNS = [
    ("pdf", re.compile(r"\bpdfs?\b", re.IGNORECASE)),
    ("xlsx", re.compile(r"\b(spreadsheets?|excel|xlsx|workbooks?|sheets?)\b", re.IGNORECASE)),
    ("pptx", re.compile(r"\b(presentations?(?! of)|slides?|slide decks?|decks?|pptx|powerpoints?)\b", re.IGNORECASE)),
    ("docx", re.compile(r"\b(word (documents?|files?)|docx)\b", re.IGNORECASE)),
]
_TIME_CUE_RE = re.compile(r"\b(modified|updated|changed|edited|added|created)\b", re.IGNORECASE)
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
# File names that are ordinary words: they name a document only when the user is explicit about it.
_GENERIC_STEMS = set(
    "notes note plan plans budget budgets contract contracts report reports minutes agenda draft summary "
    "presentation document documents file files test data info information policy policies list proposal "
    "invoice invoices memo letter template form forms readme todo schedule project projects meeting meetings "
    "agreement manual guide handbook faq overview strategy roadmap results review checklist".split()
)


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
    """Case-, accent- and punctuation-insensitive form of any script; "EmployeeHandbook2026" -> "employee handbook 2026"."""
    text = _CAMEL_RE.sub(" ", text)
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.sub(r"[\W_]+", " ", text).split())


def _stem(filename: str) -> str:
    base = filename.rsplit(".", 1)[0] if re.search(r"\.[A-Za-z0-9]{1,6}$", filename) else filename
    return _normalize(base)


def _explicit(stem: str, filename: str, raw_query: str) -> bool:
    """The query names a generic file name on purpose: with its extension, in quotes, or as "the X file"."""
    q = raw_query.casefold()
    return (
        filename.casefold() in q
        or re.search(rf"[\"'“‘]\s*{re.escape(stem)}\s*[\"'”’]", q) is not None
        or re.search(rf"\b{re.escape(stem)} (file|document|doc|spreadsheet|sheet|workbook|deck|presentation|pdf)\b",
                     _normalize(raw_query)) is not None
    )


def _name_matches(query: str, catalog: List[CatalogEntry]) -> List[CatalogEntry]:
    """Documents the query names (whole-word match of the file name without extension), longest
    names first, newest first among duplicates."""
    normalized = _normalize(query)
    q, compact = f" {normalized} ", normalized.replace(" ", "")
    hits = []
    for position, entry in enumerate(catalog):  # the catalog is newest first
        stem = _stem(entry.filename)
        if _CJK_RE.search(stem):
            start = compact.find(stem.replace(" ", "")) if len(stem.replace(" ", "")) >= 2 else -1
            span = (start, start + len(stem.replace(" ", ""))) if start >= 0 else None
            space = "compact"
        else:
            start = q.find(f" {stem} ") if len(stem) >= 4 else -1
            if start >= 0 and (stem.isdigit() or stem in _GENERIC_STEMS) and not _explicit(stem, entry.filename, query):
                start = -1
            span = (start, start + len(stem) + 2) if start >= 0 else None
            space = "words"
        if span:
            hits.append((-len(stem), position, stem, space, span, entry))
    hits.sort(key=lambda h: (h[0], h[1]))
    picked, stems, spans = [], set(), []
    for _, _, stem, space, span, entry in hits:
        if stem in stems:  # duplicate file names: the newest copy only
            continue
        if any(sp == space and a <= span[0] and span[1] <= b for sp, (a, b) in spans):
            continue  # "employee handbook" inside a longer matched name "employee handbook 2026"
        picked.append(entry)
        stems.add(stem)
        spans.append((space, span))
    return picked[:3]


def match_documents(query: str, catalog: List[CatalogEntry]) -> List[str]:
    return [entry.id for entry in _name_matches(query, catalog)]


# Words that never identify a particular document.
_GENERIC_WORDS = _SUMMARY_FILLER | {"report", "version", "final", "draft", "new", "copy"}


def _words(text: str) -> List[str]:
    return [w for w in _normalize(text).split() if len(w) >= 4 and w not in _GENERIC_WORDS and not w.isdigit()]


def loose_match(query: str, catalog: List[CatalogEntry]) -> Optional[CatalogEntry]:
    """The one document whose name shares the most distinctive words with the query ("summarize the
    handbook" -> "Employee Handbook.docx"), or None when no document stands out."""
    words = set(_words(query))
    if not words:
        return None
    scored = sorted(((len(words & set(_words(_stem(e.filename)))), -i, e) for i, e in enumerate(catalog)
                     if words & set(_words(_stem(e.filename)))), key=lambda s: (s[0], s[1]), reverse=True)
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return scored[0][2]


def _only_summary_words(query: str, names: List[str]) -> bool:
    """True when nothing but the document name(s) and summary wording is left: "Summarize the
    Employee Handbook" is a summary, "What are the key points about parental leave in the Employee
    Handbook?" is a focused question about that document."""
    named = set()
    for name in names:
        named |= set(_normalize(name).split())
    return all(w in named or w in _SUMMARY_FILLER or w.isdigit() for w in _normalize(query).split())


def _file_types(query: str) -> List[str]:
    text = _SHEET_COMPOUND_RE.sub(" ", query)  # "balance sheet" is not a spreadsheet request
    return [t for t, pattern in _FILE_TYPE_PATTERNS if pattern.search(text)]


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


def _recent_document(query: str, catalog: List[CatalogEntry]) -> Optional[CatalogEntry]:
    """'my latest document' -> the most recently modified one (the catalog is newest first)."""
    return catalog[0] if catalog and _RECENT_DOC_RE.search(query) else None


def _rule_intent(query: str, targets: List[str], names: List[str], recent: bool = False) -> str:
    if len(query) <= 80 and _GREETING_RE.match(query):
        return CONVERSATION
    if _LIST_RE.search(query) and not _NOT_INVENTORY_RE.search(query) and not recent:
        return LIST_DOCUMENTS
    asks_for_summary = _SUMMARY_RE.search(query) or (recent and _CONTENTS_RE.search(query))
    if targets and asks_for_summary and _only_summary_words(query, names):
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
    matched = _name_matches(query, catalog)
    names = [_stem(e.filename) for e in matched]
    recent = None if matched else _recent_document(query, catalog)
    if recent:
        matched = [recent]
    elif not matched and _SUMMARY_RE.search(query):
        loose = loose_match(query, catalog)
        if loose:
            matched, names = [loose], [" ".join(set(_words(query)) & set(_words(_stem(loose.filename))))]
    targets = [e.id for e in matched]
    plan = QueryPlan(
        query=query,
        search_query=query,
        intent=_rule_intent(query, targets, names, recent=bool(recent)),
        target_document_ids=targets,
        file_types=_file_types(query),
        modified_after=_modified_after(query),
    )
    if plan.intent == CONVERSATION:
        return plan
    # Self-contained questions skip the extra LLM round trip, except summary requests that name a
    # document only loosely ("summarize the policy" with two policies): the model resolves which one.
    loose_summary = bool(catalog) and not targets and bool(_SUMMARY_RE.search(query))
    if not history and not loose_summary:
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
    by_title: Dict[str, str] = {}
    for entry in catalog:  # newest first: duplicate titles resolve to the newest copy
        by_title.setdefault(entry.filename.lower(), entry.id)
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
