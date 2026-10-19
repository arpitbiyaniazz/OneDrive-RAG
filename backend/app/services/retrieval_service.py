"""Hybrid retrieval: pgvector cosine search + Postgres full-text search, fused with RRF."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from sqlalchemy import String, cast, func, literal_column, select, text
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chunk import DocumentChunk
from app.models.document import INDEXED, Document
from app.services.embedding_service import get_embedding_provider
from app.services.query_engine import CatalogEntry, QueryPlan

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


def _base(user_id: str, model_id: str, document_ids: Optional[Sequence[str]], file_types: Sequence[str],
          modified_after: Optional[datetime]):
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


async def count_indexed(db: AsyncSession, user_id: str) -> int:
    return (await db.execute(
        select(func.count(Document.id)).where(Document.user_id == user_id, Document.status == INDEXED)
    )).scalar_one()


async def load_catalog(db: AsyncSession, user_id: str, limit: int = 5000) -> List[CatalogEntry]:
    rows = (await db.execute(
        select(Document.id, Document.filename, Document.file_type, Document.folder_path, Document.modified_date, Document.drive_type)
        .where(Document.user_id == user_id, Document.status == INDEXED)
        .order_by(Document.modified_date.desc().nullslast(), Document.filename)
        .limit(limit)
    )).all()
    return [CatalogEntry(r.id, r.filename, r.file_type, r.folder_path, r.modified_date, r.drive_type) for r in rows]
