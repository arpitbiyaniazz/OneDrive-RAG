"""Query understanding: follow-up rewriting, intent, document-name targeting, and the topic and date
filters of document-list questions ("how many documents are about AI", "files from today")."""
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

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
# "How many documents must be submitted...", "what do the documents about pricing say": content questions.
_CONTENT_CUE_RE = re.compile(
    r"\b(must|should|need(ed)?|required|necessary|submit(ted)?|bring|due|deadlines?|expir(e|es|ed|ing|y)|"
    r"say|says|said|state|states|stated)\b",
    re.IGNORECASE,
)
# Document-list questions with a filter: "which / how many / list / any ... documents" plus a topic or a date.
_INVENTORY_HEAD_RE = re.compile(
    rf"\b(which|how many|number of|count|list|show|find|any|all)\b(\s+[\w-]+){{0,4}}?\s+{_LIST_NOUNS}\b"
    r"|\bwhat\s+([\w-]+\s+){0,3}?(documents|files|docs|spreadsheets|workbooks|presentations|decks|pdfs)\b"
    rf"|^\s*([\w-]+\s+){{0,3}}?{_LIST_NOUNS}\b"
    r"|\bwhat (did|have) (i|we) (upload|add|create|modify|edit|change|sync)(ed)?\b",
    re.IGNORECASE,
)
_TOPIC_RE = re.compile(
    r"\b(?:about|regarding|concerning|on the (?:topic|subject) of|(?:related|relating|relevant) to|"
    r"(?:that|which|documents?|files?|docs|pdfs?|spreadsheets?|presentations?) (?:mentions?|covers?|discuss(?:es)?|"
    r"talks? about|refers? to|contains?|deals? with)|mentioning|covering|discussing|containing|referring to|"
    r"dealing with)\s+(?P<topic>.+)",
    re.IGNORECASE,
)
# "how many AI documents", "list all HR-related files": the words before the noun are the topic.
_ADJECTIVE_TOPIC_RE = re.compile(
    rf"\b(?:which|how many|number of|count|list|show|find|any|all|what)\s+(?P<words>(?:[\w-]+\s+){{1,3}}?){_LIST_NOUNS}\b",
    re.IGNORECASE,
)
# Words around a document-list question that are not part of its topic.
_INVENTORY_FILLER = set(
    "my all the our your these those of any me some recent latest newest new old older oldest indexed synced "
    "uploaded added total pdf pdfs word excel powerpoint google onedrive drive text kind kinds type types sort "
    "sorts different other many more much do does i have has are there is in on a an important main shared "
    "related based one two three four five six seven eight nine ten first top".split()
)
_TOPIC_TAIL_RE = re.compile(
    r"\s+(?:do (?:i|we) have|have (?:i|we)|are there|is there|i have|we have|(?:are|were) (?:indexed|synced|available)"
    r"|in (?:my|the|our) (?:drive|onedrive|google drive|knowledge base|documents|files|folders?))\b.*$",
    re.IGNORECASE,
)
# "in the Finance folder", "in folder HR": the name is checked against the user's folder names.
_FOLDER_NAME = r"(?:\"[^\"]+\"|“[^”]+”|'[^']+'|[\w&.-]+(?:\s+[\w&.-]+){0,3}?)"
_FOLDER_RE = re.compile(
    r"\b(?:in|inside|under|within|from)\s+(?:the\s+|my\s+|our\s+)?"
    rf"(?:(?:folder|directory)\s+(?:called\s+|named\s+)?(?P<named>{_FOLDER_NAME})"
    r"(?=\s*(?:$|[?!.,;])|\s+(?:about|that|which|with|from|modified|updated|created|added|on|in|and)\b)"
    rf"|(?P<before>{_FOLDER_NAME})\s+(?:folder|directory)\b)",
    re.IGNORECASE,
)
# "which documents mention X" asks for every document containing X, not a judgement of what it is about.
_MENTION_RE = re.compile(r"\b(?:mention(?:s|ing|ed)?|contain(?:s|ing)?|refer(?:s|ring)?\s+to)\b", re.IGNORECASE)
# A file name with its extension ("Budget 2026.xlsx") names a document, not a file type to filter by.
_FILE_NAME_TOKEN_RE = re.compile(r"[\w-]+\.(?:pdf|docx?|docm|dotx|xlsx|xlsm|xltx|xls|pptx|pptm|ppt|csv|txt|md|html?|json|xml)\b",
                                 re.IGNORECASE)
_TOPIC_EDGE_WORDS = set("and or from of on in during that which who were are is was modified updated created uploaded "
                        "added dated indexed synced the a an my our any".split())

# Dates. Times are compared in the user's time zone ("today" is their today).
ANY_DATE, MODIFIED, CREATED, INDEXED_DATE = "any", "modified", "created", "indexed"
_MONTHS = {name: number for number, names in enumerate(
    ["january jan", "february feb", "march mar", "april apr", "may", "june jun", "july jul", "august aug",
     "september sept sep", "october oct", "november nov", "december dec"], start=1) for name in names.split()}
_MONTH = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)"
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b|\b(\d{1,2})-(\d{1,2})-(\d{4})\b")
_DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+{_MONTH}\b\.?(?:,?\s+(\d{{4}}))?", re.IGNORECASE)
_MONTH_DAY_RE = re.compile(rf"\b{_MONTH}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.IGNORECASE)
_MONTH_YEAR_RE = re.compile(rf"\b(?:in|from|during|of)\s+{_MONTH}\b(?:\s+(\d{{4}}))?|\b{_MONTH}\s+(\d{{4}})\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:in|from|during|of)\s+((?:19|20)\d{2})\b(?![-/])", re.IGNORECASE)
_PAST_RE = re.compile(r"\b(?:past|last|previous)\s+(\d{1,3})\s+(days?|weeks?|months?)\b", re.IGNORECASE)
_RELATIVE_DATES = ("today", "yesterday", "this week", "last week", "past week", "previous week", "this month",
                   "last month", "past month", "previous month", "this year", "last year", "previous year")
_RELATIVE_DATE_RE = re.compile(r"\b(" + "|".join(re.escape(p) for p in _RELATIVE_DATES) + r")\b", re.IGNORECASE)
_SAME_DATE_RE = re.compile(r"\bsame (?:date|day)\b", re.IGNORECASE)
_CREATED_CUE_RE = re.compile(r"\b(creat(e|es|ed)|upload(s|ed)?|add(s|ed)?|new)\b", re.IGNORECASE)
_MODIFIED_CUE_RE = re.compile(r"\b(modif(y|ies|ied)|updat(e|es|ed)|chang(e|es|ed)|edit(s|ed)?)\b", re.IGNORECASE)
_INDEXED_CUE_RE = re.compile(r"\b(indexed|synced|ingested)\b", re.IGNORECASE)
_DATE_PHRASE_RE = re.compile(
    rf"\s*\b(?:(?:from|of|on|in|during|since|dated|modified|updated|changed|edited|created|uploaded|added|indexed|"
    rf"synced)\s+)*(?:{_RELATIVE_DATE_RE.pattern}|{_PAST_RE.pattern}|{_ISO_DATE_RE.pattern}|{_NUMERIC_DATE_RE.pattern}|"
    rf"{_DAY_MONTH_RE.pattern}|{_MONTH_DAY_RE.pattern}|{_MONTH_YEAR_RE.pattern}|{_YEAR_RE.pattern}|"
    r"(?:the )?same (?:date|day)(?: as\b.*)?)",
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
_FILE_TYPE_WORD_RE = re.compile(
    r"pdfs?|excel|xlsx|docx|pptx|csv|txt|md|markdown|word|powerpoints?|spreadsheets?|workbooks?|presentations?|"
    r"slides?|decks?", re.IGNORECASE)
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
    created_date: Optional[datetime] = None
    indexed_at: Optional[datetime] = None
    url: Optional[str] = None


@dataclass
class DateRange:
    """[start, end) in the user's time zone, on one date of the document."""
    start: datetime
    end: datetime
    field: str = ANY_DATE  # any (modified or added) | modified | created | indexed

    def describe(self) -> str:
        what = {ANY_DATE: "modified or added", MODIFIED: "modified", CREATED: "added", INDEXED_DATE: "indexed"}[self.field]
        first, last = self.start.date(), (self.end - timedelta(days=1)).date()
        return f"{what} on {first.isoformat()}" if first == last else f"{what} between {first.isoformat()} and {last.isoformat()}"


@dataclass
class QueryPlan:
    query: str
    search_query: str
    intent: str = DOCUMENT_QUESTION
    target_document_ids: List[str] = field(default_factory=list)
    file_types: List[str] = field(default_factory=list)
    modified_after: Optional[datetime] = None
    rewritten: bool = False
    # Document-list questions: only documents about this topic and/or with a date in this range.
    topic: Optional[str] = None
    topic_mode: str = "about"  # "mentions": every document containing the topic's words
    date_range: Optional[DateRange] = None
    folder: Optional[str] = None  # documents in this folder (or its subfolders)


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
    text = _FILE_NAME_TOKEN_RE.sub(" ", query)  # "same date as Budget 2026.xlsx" is not about spreadsheets
    text = _SHEET_COMPOUND_RE.sub(" ", text)  # "balance sheet" is not a spreadsheet request
    return [t for t, pattern in _FILE_TYPE_PATTERNS if pattern.search(text)]


def _modified_after(query: str, now: datetime) -> Optional[datetime]:
    if not _TIME_CUE_RE.search(query):
        return None
    q = query.lower()
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


def _date_field(query: str) -> str:
    if _INDEXED_CUE_RE.search(query):
        return INDEXED_DATE
    created, modified = _CREATED_CUE_RE.search(query), _MODIFIED_CUE_RE.search(query)
    if created and modified:
        return ANY_DATE  # "modified or added on ...", also how answers describe an any-date filter
    if created:
        return CREATED
    return MODIFIED if modified else ANY_DATE


def _days(first: date, last: date, now: datetime, query: str) -> Optional[DateRange]:
    """Whole local days first..last."""
    if last < first:
        return None
    start = datetime.combine(first, time.min, tzinfo=now.tzinfo)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=now.tzinfo)
    return DateRange(start, end, _date_field(query))


def _month_span(year: int, month: int) -> Tuple[date, date]:
    following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return date(year, month, 1), following - timedelta(days=1)


def _day(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _past_year(month: int, day: int, today: date) -> int:
    """A date named without a year is the most recent one: "December" asked in January is last December."""
    return today.year if (month, day) <= (today.month, today.day) else today.year - 1


def date_range(query: str, now: datetime) -> Optional[DateRange]:
    """The dates a document-list question asks about: today, yesterday, this/last week, month or year,
    the past N days, weeks or months, a day (2026-10-20, 20/10/2026, 20 October, Oct 20 2026), a month
    (in October, October 2026) or a year (in 2025). Numeric dates are read day first."""
    today = now.date()
    relative = _RELATIVE_DATE_RE.search(query)
    if relative:
        phrase = relative.group(1).lower()
        monday = today - timedelta(days=today.weekday())
        first_of_month = today.replace(day=1)
        spans = {
            "today": (today, today),
            "yesterday": (today - timedelta(days=1), today - timedelta(days=1)),
            "this week": (monday, today),
            "past week": (today - timedelta(days=6), today),
            "this month": (first_of_month, today),
            "past month": (today - timedelta(days=29), today),
            "this year": (date(today.year, 1, 1), today),
        }
        if phrase in ("last week", "previous week"):
            spans[phrase] = (monday - timedelta(days=7), monday - timedelta(days=1))
        elif phrase in ("last month", "previous month"):
            spans[phrase] = _month_span(*((today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)))
        elif phrase in ("last year", "previous year"):
            spans[phrase] = (date(today.year - 1, 1, 1), date(today.year - 1, 12, 31))
        return _days(*spans[phrase], now, query)
    past = _PAST_RE.search(query)
    if past:
        count, unit = min(int(past.group(1)), 3650), past.group(2).lower()
        days = count * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
        return _days(today - timedelta(days=max(days, 1) - 1), today, now, query)
    match = _ISO_DATE_RE.search(query)
    if match:
        day = _day(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return _days(day, day, now, query) if day else None
    match = _NUMERIC_DATE_RE.search(query)
    if match:
        first, second, year = (match.group(1), match.group(2), match.group(3)) if match.group(1) else match.group(4, 5, 6)
        year = int(year) + (2000 if len(year) == 2 else 0)
        day_first, month_first = _day(year, int(second), int(first)), _day(year, int(first), int(second))
        day = day_first or month_first  # 20/10/2026 and 10/20/2026 both mean 20 October
        return _days(day, day, now, query) if day else None
    for pattern, day_group, month_group in ((_DAY_MONTH_RE, 1, 2), (_MONTH_DAY_RE, 2, 1)):
        match = pattern.search(query)
        if match:
            month, number = _MONTHS[match.group(month_group).lower()], int(match.group(day_group))
            year = int(match.group(3)) if match.group(3) else _past_year(month, number, today)
            day = _day(year, month, number)
            if day:
                return _days(day, day, now, query)
    match = _MONTH_YEAR_RE.search(query)
    if match:
        name, year = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
        month = _MONTHS[name.lower()]
        return _days(*_month_span(int(year) if year else _past_year(month, 1, today), month), now, query)
    match = _YEAR_RE.search(query)
    if match:
        year = int(match.group(1))
        return _days(date(year, 1, 1), date(year, 12, 31), now, query)
    return None


def _local_date(value: Optional[datetime], now: datetime) -> Optional[date]:
    return value.astimezone(now.tzinfo).date() if value else None


def _same_date_as(query: str, matched: List[CatalogEntry], now: datetime) -> Optional[DateRange]:
    """'documents with the same date as Budget 2026.xlsx': the named document's (local) date."""
    if not matched or not _SAME_DATE_RE.search(query):
        return None
    anchor, field_name = matched[0], _date_field(query)
    value = {CREATED: anchor.created_date, INDEXED_DATE: anchor.indexed_at}.get(field_name) or anchor.modified_date
    day = _local_date(value, now)
    return _days(day, day, now, query) if day else None


def _is_edge(word: str) -> bool:
    return word.lower() in _TOPIC_EDGE_WORDS or bool(_FILE_TYPE_WORD_RE.fullmatch(word))


def _clean_topic(text: str) -> Optional[str]:
    text = _DATE_PHRASE_RE.sub(" ", text)
    text = _FOLDER_RE.sub(" ", text)
    text = _TOPIC_TAIL_RE.sub("", text)
    # "Kubernetes are PDFs" (a rewritten "which of those are PDFs?"): the clause after the topic is a filter.
    text = re.sub(rf"\s+(?:are|is|were|was)\s+(?:all\s+|the\s+|only\s+)?(?:{_FILE_TYPE_WORD_RE.pattern}|from|in|on|"
                  r"modified|updated|created|added|uploaded|dated|indexed|synced)\b.*$", "", text, flags=re.IGNORECASE)
    words = re.sub(r"[?!.,;:]+(?=\s|$)", " ", text).split()  # end-of-word punctuation only: keeps "4.2.1"
    while words and _is_edge(words[0]):
        words.pop(0)
    while words and _is_edge(words[-1]):
        words.pop()
    topic = " ".join(words)[:200]
    return topic if re.search(r"\w", topic) else None


def topic_of(query: str) -> Optional[str]:
    """The subject of a document-list question: "how many documents are about AI" -> "AI",
    "which files mention Contoso" -> "Contoso", "list all HR-related documents" -> "HR"."""
    match = _TOPIC_RE.search(query)
    if match:
        return _clean_topic(match.group("topic"))
    match = _ADJECTIVE_TOPIC_RE.search(query)
    if match:
        words = [re.sub(r"-(related|based)$", "", w, flags=re.IGNORECASE) for w in match.group("words").split()]
        kept = [w for w in words if w.lower() not in _INVENTORY_FILLER and not _FILE_TYPE_WORD_RE.fullmatch(w)]
        return " ".join(kept) or None
    return None


def topic_mode(query: str) -> str:
    """"mentions" when the question asks which documents mention or contain the topic."""
    match = _TOPIC_RE.search(query)
    return "mentions" if match and _MENTION_RE.search(query, match.start(), match.start("topic")) else "about"


def folder_of(query: str, catalog: List[CatalogEntry]) -> Optional[str]:
    """'in the Finance folder' -> "Finance", spelled like the user's folder when one has that name."""
    match = _FOLDER_RE.search(query)
    if not match:
        return None
    name = (match.group("named") or match.group("before") or "").strip().strip("\"'“”")
    if not name or name.lower() in ("my", "the", "this", "that", "same", "a", "any"):
        return None
    known = {part for entry in catalog for part in entry.folder_path.split("/") if part}
    return next((k for k in known if k.lower() == name.lower()), name)


def _inventory_filters(query: str, catalog: List[CatalogEntry], now: datetime
                       ) -> Tuple[Optional[str], Optional[DateRange], Optional[str]]:
    anchor = _name_matches(query, catalog) if _SAME_DATE_RE.search(query) else []
    return topic_of(query), _same_date_as(query, anchor, now) or date_range(query, now), folder_of(query, catalog)


def _recent_document(query: str, catalog: List[CatalogEntry]) -> Optional[CatalogEntry]:
    """'my latest document' -> the most recently modified one (the catalog is newest first)."""
    return catalog[0] if catalog and _RECENT_DOC_RE.search(query) else None


def _is_inventory(query: str, filtered: bool) -> bool:
    """Asks which documents exist (optionally about a topic or from a date), not what they say."""
    if _CONTENT_CUE_RE.search(query):
        return False
    if _LIST_RE.search(query):
        return True
    return filtered and bool(_INVENTORY_HEAD_RE.search(query)) and not _SUMMARY_RE.search(query)


def _rule_intent(query: str, targets: List[str], names: List[str], recent: bool = False, filtered: bool = False) -> str:
    if len(query) <= 80 and _GREETING_RE.match(query):
        return CONVERSATION
    if not recent and _is_inventory(query, filtered):
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


async def plan_query(query: str, history: List[Dict[str, str]], catalog: List[CatalogEntry],
                     now: Optional[datetime] = None) -> QueryPlan:
    """`now` is the current time in the user's time zone (dates such as "today" are theirs)."""
    query = query.strip()
    now = now or datetime.now(UTC)
    topic, dates, folder = _inventory_filters(query, catalog, now)
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
        intent=_rule_intent(query, targets, names, recent=bool(recent), filtered=bool(topic or dates or folder)),
        target_document_ids=targets,
        file_types=_file_types(query),
        modified_after=_modified_after(query, now),
    )
    if plan.intent == LIST_DOCUMENTS:
        plan.topic, plan.date_range, plan.folder = topic, dates, folder
        plan.topic_mode = topic_mode(query) if topic else "about"
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
    # The model's JSON is untrusted: a field of the wrong type is ignored rather than failing the question.
    intent = data.get("intent")
    if isinstance(intent, str) and intent in INTENTS:
        plan.intent = intent
    by_title: Dict[str, str] = {}
    for entry in catalog:  # newest first: duplicate titles resolve to the newest copy
        by_title.setdefault(entry.filename.lower(), entry.id)
    titles = data.get("target_documents")
    titles = [t for t in titles if isinstance(t, str)] if isinstance(titles, list) else []
    named = [by_title[t.lower()] for t in titles if t.lower() in by_title]
    if named:
        plan.target_document_ids = list(dict.fromkeys(named + plan.target_document_ids))[:3]
    else:
        plan.target_document_ids = match_documents(plan.search_query, catalog) or plan.target_document_ids
    if plan.intent == SUMMARIZE and not plan.target_document_ids:
        plan.intent = DOCUMENT_QUESTION
    plan.file_types = plan.file_types or _file_types(plan.search_query)
    plan.modified_after = plan.modified_after or _modified_after(plan.search_query, now)
    if plan.intent == LIST_DOCUMENTS:
        # A follow-up ("and which of them are from today?") gets its filters from the rewritten question.
        new_topic, new_dates, new_folder = (_inventory_filters(plan.search_query, catalog, now) if plan.rewritten
                                            else (None, None, None))
        plan.topic, plan.date_range, plan.folder = new_topic or topic, new_dates or dates, new_folder or folder
        plan.topic_mode = topic_mode(plan.search_query if new_topic else query) if plan.topic else "about"
    else:
        plan.topic = plan.date_range = plan.folder = None
    return plan
