"""Hybrid retrieval: pgvector cosine search + Postgres full-text search, fused with RRF."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import String, and_, cast, func, literal_column, or_, select, text
from sqlalchemy.dialects.postgresql import TSQUERY, distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chunk import DocumentChunk
from app.models.document import INDEXED, Document
from app.services.embedding_service import get_embedding_provider
from app.services.query_engine import ANY_DATE, CREATED, INDEXED_DATE, CatalogEntry, DateRange, QueryPlan

_RRF_K = 60
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = set(
    "a an and are as at be but by can could did do does for from had has have how i if in into is it its me my "
    "no not of on or our please should so than that the their them then there these they this to us was we were "
    "what when where which who whom why will with would you your about tell give show find any all".split()
)


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    filename: str
    file_type: str
    drive_type: str
    folder_path: str
    url: Optional[str]
    modified_date: Optional[datetime]
    chunk_index: int
    content: str
    section: Optional[str]
    page_start: Optional[int]
    page_end: Optional[int]
    location: Optional[str] = None
    score: float = 0.0
    signals: Dict[str, int] = field(default_factory=dict)


def _location(row) -> Optional[str]:
    parts = []
    if row.section:
        parts.append(row.section)
    if row.page_number and row.file_type == "pdf":
        parts.append(f"page {row.page_number}" if row.page_end in (None, row.page_number) else f"pages {row.page_number}-{row.page_end}")
    rows = (row.metadata_json or {}).get("rows") if row.metadata_json else None
    if rows and not (row.section and "rows" in row.section):
        parts.append(f"rows {rows}")
    return ", ".join(parts) or None


def _columns():
    return (
        DocumentChunk.id, DocumentChunk.document_id, DocumentChunk.chunk_index, DocumentChunk.content,
        DocumentChunk.section, DocumentChunk.page_number, DocumentChunk.page_end, DocumentChunk.metadata_json,
        Document.filename, Document.file_type, Document.drive_type, Document.folder_path, Document.onedrive_url,
        Document.modified_date,
    )


def _to_chunk(row) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=row.id, document_id=row.document_id, filename=row.filename, file_type=row.file_type,
        drive_type=row.drive_type, folder_path=row.folder_path, url=row.onedrive_url, modified_date=row.modified_date,
        chunk_index=row.chunk_index, content=row.content, section=row.section, page_start=row.page_number,
        page_end=row.page_end, location=_location(row),
    )


def _date_clause(dates: DateRange):
    def within(column):
        return and_(column >= dates.start, column < dates.end)

    if dates.field == ANY_DATE:  # "documents of today": changed or added that day
        return or_(within(Document.modified_date), within(Document.created_date))
    return within({CREATED: Document.created_date, INDEXED_DATE: Document.last_ingested_at}.get(dates.field,
                                                                                             Document.modified_date))


def _folder_clause(folder: str):
    """The folder and its subfolders, wherever it sits in the path (case-insensitive)."""
    name = folder.strip("/").lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    path = func.lower(Document.folder_path)
    return or_(path.like(f"%/{name}", escape="\\"), path.like(f"%/{name}/%", escape="\\"))


def _base(user_id: str, model_id: str, document_ids: Optional[Sequence[str]], file_types: Sequence[str],
          modified_after: Optional[datetime], date_range: Optional[DateRange] = None, folder: Optional[str] = None):
    stmt = (
        select(*_columns())
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.user_id == user_id, Document.user_id == user_id, Document.status == INDEXED)
        .where(DocumentChunk.embedding_model == model_id)
    )
    if document_ids:
        stmt = stmt.where(DocumentChunk.document_id.in_(list(document_ids)))
    if file_types:
        stmt = stmt.where(Document.file_type.in_(list(file_types)))
    if modified_after:
        stmt = stmt.where(Document.modified_date >= modified_after)
    if date_range:
        stmt = stmt.where(_date_clause(date_range))
    if folder:
        stmt = stmt.where(_folder_clause(folder))
    return stmt


def _keyword_text(text: str) -> Optional[str]:
    """The question without stopwords and search operators. Postgres then tokenizes it with the same
    parser as the indexed text, so e-mails, codes (ZX-4471), versions (4.2.1) and decimals match."""
    kept = []
    for token in text.split():
        bare = token.strip(".,;:!?()[]{}\"'“”‘’").lower()
        if not bare or bare in _STOPWORDS or bare in ("or", "and", "not"):
            continue
        kept.append(token.lstrip("-").replace('"', " "))  # a leading "-" would mean NOT, quotes mean phrases
    return " ".join(kept[:32]) or None


def _tsquery(text: str):
    """OR over the question's terms, built by websearch_to_tsquery (which ANDs them)."""
    anded = cast(func.websearch_to_tsquery(literal_column("'simple'"), text), String)
    return cast(func.replace(anded, " & ", " | "), TSQUERY)


_iterative_scan: Optional[bool] = None


def _version(value: Optional[str]) -> tuple:
    try:
        return tuple(int(p) for p in (value or "0").split(".")[:3])
    except ValueError:
        return (0,)


async def _filtered_hnsw(db) -> None:
    """The HNSW index is shared by all users and pgvector filters *after* the index scan, so a small
    tenant could get only a handful of its own chunks back. Iterative scans (pgvector 0.8+) keep
    scanning until enough rows pass the user/model filters."""
    global _iterative_scan
    if _iterative_scan is None:
        version = (await db.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))).scalar()
        _iterative_scan = _version(version) >= (0, 8, 0)
    if _iterative_scan:
        await db.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        await db.execute(text(f"SET LOCAL hnsw.ef_search = {min(1000, max(100, 2 * settings.RETRIEVAL_CANDIDATES))}"))


async def _vector_search(db, query_vector, limit, **filters) -> List:
    await _filtered_hnsw(db)
    distance = DocumentChunk.embedding.cosine_distance(query_vector)
    stmt = _base(**filters).add_columns(distance.label("distance")).where(DocumentChunk.embedding.is_not(None))
    return (await db.execute(stmt.order_by(distance).limit(limit))).all()


async def _keyword_search(db, text: str, limit, **filters) -> List:
    query = _keyword_text(text)
    if not query:
        return []
    tsq = _tsquery(query)
    rank = func.ts_rank_cd(DocumentChunk.search_vector, tsq)
    stmt = _base(**filters).add_columns(rank.label("rank")).where(DocumentChunk.search_vector.op("@@")(tsq))
    return (await db.execute(stmt.order_by(rank.desc()).limit(limit))).all()


def _fuse(result_lists: List[tuple[str, List]], top_k: int, per_doc_cap: int) -> List[RetrievedChunk]:
    """Reciprocal rank fusion; "hint_" lists (file-type/date cues) count at _BOOST_WEIGHT."""
    fused: Dict[str, RetrievedChunk] = {}
    for signal, rows in result_lists:
        weight = _BOOST_WEIGHT if signal.startswith("hint_") else 1.0
        for rank, row in enumerate(rows):
            chunk = fused.get(row.id) or _to_chunk(row)
            chunk.score += weight / (_RRF_K + rank + 1)
            chunk.signals[signal] = rank + 1
            fused[row.id] = chunk
    ordered = sorted(fused.values(), key=lambda c: c.score, reverse=True)
    picked, per_doc = [], {}
    for chunk in ordered:
        if per_doc.get(chunk.document_id, 0) >= per_doc_cap:
            continue
        per_doc[chunk.document_id] = per_doc.get(chunk.document_id, 0) + 1
        picked.append(chunk)
        if len(picked) >= top_k:
            break
    return picked


_BOOST_WEIGHT = 0.5  # file-type / date hints raise matching passages without hiding the others


async def hybrid_search(db: AsyncSession, user_id: str, plan: QueryPlan, top_k: Optional[int] = None) -> List[RetrievedChunk]:
    top_k = top_k or settings.RETRIEVAL_TOP_K
    provider = get_embedding_provider()
    query_vector = await provider.embed_query(plan.search_query)
    candidates = settings.RETRIEVAL_CANDIDATES

    async def lists(document_ids=None, file_types=(), modified_after=None, prefix=""):
        filters = dict(user_id=user_id, model_id=provider.model_id, document_ids=document_ids,
                       file_types=file_types, modified_after=modified_after)
        return [(f"{prefix}vector", await _vector_search(db, query_vector, candidates, **filters)),
                (f"{prefix}keyword", await _keyword_search(db, plan.search_query, candidates, **filters))]

    results: List[RetrievedChunk] = []
    targets = plan.target_document_ids
    if len(targets) == 1:
        # The user named a document: its best passages first, then the rest of the knowledge base.
        results = _fuse(await lists(document_ids=targets), max(4, top_k // 2), top_k)
    elif targets:
        # Several named documents (comparisons): the same share of passages from each.
        share = max(2, top_k // len(targets))
        for doc_id in targets:
            results += _fuse(await lists(document_ids=[doc_id]), share, share)

    signals = await lists()
    if plan.file_types or plan.modified_after:
        # "in my spreadsheets", "updated this month": a boost, never a hard filter. A cue can be wrong
        # ("balance sheet" in a PDF) and must not push the answer out.
        signals += await lists(file_types=plan.file_types, modified_after=plan.modified_after, prefix="hint_")
    seen = {r.chunk_id for r in results}
    general = [c for c in _fuse(signals, top_k + len(seen), settings.RETRIEVAL_MAX_CHUNKS_PER_DOCUMENT)
               if c.chunk_id not in seen]
    return (results + general)[:top_k]


def sample_evenly(chunks: List[RetrievedChunk], max_chars: int) -> List[RetrievedChunk]:
    """Keeps passages spread evenly over the document so the total fits `max_chars`."""
    total = sum(len(c.content) for c in chunks)
    if total <= max_chars or not chunks:
        return chunks
    keep = max(1, int(len(chunks) * max_chars / total))
    step = len(chunks) / keep
    return [chunks[int(i * step)] for i in range(keep)]


async def document_chunks_in_order(db: AsyncSession, user_id: str, document_id: str,
                                   max_chars: Optional[int] = None) -> List[RetrievedChunk]:
    """A document's chunks in reading order (sampled evenly to fit `max_chars` when given)."""
    stmt = _base(user_id, get_embedding_provider().model_id, [document_id], (), None).order_by(DocumentChunk.chunk_index)
    chunks = [_to_chunk(row) for row in (await db.execute(stmt)).all()]
    return sample_evenly(chunks, max_chars) if max_chars else chunks


def _indexed_documents(user_id: str, file_types: Sequence[str] = (), date_range: Optional[DateRange] = None,
                       folder: Optional[str] = None):
    conditions = [Document.user_id == user_id, Document.status == INDEXED]
    if file_types:
        conditions.append(Document.file_type.in_(list(file_types)))
    if date_range:
        conditions.append(_date_clause(date_range))
    if folder:
        conditions.append(_folder_clause(folder))
    return conditions


async def count_indexed(db: AsyncSession, user_id: str, file_types: Sequence[str] = (),
                        date_range: Optional[DateRange] = None, folder: Optional[str] = None) -> int:
    return (await db.execute(
        select(func.count(Document.id)).where(*_indexed_documents(user_id, file_types, date_range, folder))
    )).scalar_one()


async def load_catalog(db: AsyncSession, user_id: str, limit: int = 5000, file_types: Sequence[str] = (),
                       date_range: Optional[DateRange] = None, folder: Optional[str] = None) -> List[CatalogEntry]:
    """Indexed documents, newest first, optionally only some file types, dates or a folder."""
    rows = (await db.execute(
        select(Document.id, Document.filename, Document.file_type, Document.folder_path, Document.modified_date,
               Document.drive_type, Document.created_date, Document.last_ingested_at, Document.onedrive_url)
        .where(*_indexed_documents(user_id, file_types, date_range, folder))
        .order_by(Document.modified_date.desc().nullslast(), Document.filename)
        .limit(limit)
    )).all()
    return [CatalogEntry(r.id, r.filename, r.file_type, r.folder_path, r.modified_date, r.drive_type,
                         r.created_date, r.last_ingested_at, r.onedrive_url) for r in rows]


async def documents_about(db: AsyncSession, user_id: str, plan: QueryPlan, limit: int) -> Tuple[List[RetrievedChunk], bool]:
    """Every document's best passage for the plan's topic, most relevant documents first (at most `limit`),
    within the plan's file types and dates. The second value is True when more than `limit` documents
    contain the topic's words, so the list may be incomplete.

    Ranks whole documents rather than passages: a long document cannot push others out of the list.
    Every passage is marked with signals["mentions"]: whether its document has a passage containing all
    of the topic's words. For "which documents mention X" only those documents are returned, so the list
    is exact instead of a judgement by the model.
    """
    provider = get_embedding_provider()
    filters = dict(user_id=user_id, model_id=provider.model_id, document_ids=None, file_types=plan.file_types,
                   modified_after=None, date_range=plan.date_range, folder=plan.folder)
    keywords = _keyword_text(plan.topic)
    containing: List = []
    if keywords:
        every_word = func.websearch_to_tsquery(literal_column("'simple'"), keywords)  # AND of the words
        rank_all = func.ts_rank_cd(DocumentChunk.search_vector, every_word)
        best_all = (_base(**filters).add_columns(rank_all.label("rank"))
                    .where(DocumentChunk.search_vector.op("@@")(every_word))
                    .ext(distinct_on(DocumentChunk.document_id)).order_by(DocumentChunk.document_id, rank_all.desc())
                    .subquery())
        containing = (await db.execute(select(best_all).order_by(best_all.c.rank.desc()).limit(limit + 1))).all()
    if plan.topic_mode == "mentions" and keywords:
        found = [_to_chunk(row) for row in containing[:limit]]
        for chunk in found:
            chunk.signals["mentions"] = 1
        return found, len(containing) > limit
    mentioned = {row.document_id for row in containing}
    query_vector = await provider.embed_query(plan.topic)
    distance = DocumentChunk.embedding.cosine_distance(query_vector)
    nearest = (_base(**filters).add_columns(distance.label("distance")).where(DocumentChunk.embedding.is_not(None))
               .ext(distinct_on(DocumentChunk.document_id)).order_by(DocumentChunk.document_id, distance).subquery())
    by_meaning = (await db.execute(select(nearest).order_by(nearest.c.distance).limit(limit * 3))).all()

    by_words: List = []
    if keywords:
        tsq = _tsquery(keywords)
        rank = func.ts_rank_cd(DocumentChunk.search_vector, tsq)
        best = (_base(**filters).add_columns(rank.label("rank")).where(DocumentChunk.search_vector.op("@@")(tsq))
                .ext(distinct_on(DocumentChunk.document_id)).order_by(DocumentChunk.document_id, rank.desc()).subquery())
        by_words = (await db.execute(select(best).order_by(best.c.rank.desc()).limit(limit + 1))).all()

    # Reciprocal rank fusion per document (its best passage by meaning and by words are often different
    # passages). The passage shown is the one containing the topic's words when there is one.
    scores: Dict[str, float] = {}
    passages: Dict[str, RetrievedChunk] = {}
    for signal, rows in (("keyword", by_words), ("vector", by_meaning)):
        for rank, row in enumerate(rows):
            scores[row.document_id] = scores.get(row.document_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
            passages.setdefault(row.document_id, _to_chunk(row)).signals[signal] = rank + 1
    ranked = sorted(scores, key=lambda doc_id: scores[doc_id], reverse=True)[:limit]
    for doc_id in ranked:
        passages[doc_id].score = scores[doc_id]
        passages[doc_id].signals["mentions"] = int(doc_id in mentioned)
    return [passages[doc_id] for doc_id in ranked], len(by_words) > limit
