"""Hybrid retrieval: pgvector cosine search + Postgres full-text search, fused with RRF."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from sqlalchemy import func, literal_column, select
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


def _tsquery(text: str) -> Optional[str]:
    terms = []
    for word in _WORD_RE.findall(text.lower()):
        if word not in _STOPWORDS and (len(word) > 1 or word.isdigit()) and word not in terms:
            terms.append(word)
    return " | ".join(terms[:32]) or None


async def _vector_search(db, query_vector, limit, **filters) -> List:
    distance = DocumentChunk.embedding.cosine_distance(query_vector)
    stmt = _base(**filters).add_columns(distance.label("distance")).where(DocumentChunk.embedding.is_not(None))
    return (await db.execute(stmt.order_by(distance).limit(limit))).all()


async def _keyword_search(db, text: str, limit, **filters) -> List:
    query = _tsquery(text)
    if not query:
        return []
    tsq = func.to_tsquery(literal_column("'simple'"), query)
    rank = func.ts_rank_cd(DocumentChunk.search_vector, tsq)
    stmt = _base(**filters).add_columns(rank.label("rank")).where(DocumentChunk.search_vector.op("@@")(tsq))
    return (await db.execute(stmt.order_by(rank.desc()).limit(limit))).all()


def _fuse(result_lists: List[tuple[str, List]], top_k: int, per_doc_cap: int) -> List[RetrievedChunk]:
    fused: Dict[str, RetrievedChunk] = {}
    for signal, rows in result_lists:
        for rank, row in enumerate(rows):
            chunk = fused.get(row.id) or _to_chunk(row)
            chunk.score += 1.0 / (_RRF_K + rank + 1)
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


async def hybrid_search(db: AsyncSession, user_id: str, plan: QueryPlan, top_k: Optional[int] = None) -> List[RetrievedChunk]:
    top_k = top_k or settings.RETRIEVAL_TOP_K
    provider = get_embedding_provider()
    query_vector = await provider.embed_query(plan.search_query)
    candidates = settings.RETRIEVAL_CANDIDATES

    async def run(document_ids=None, file_types=(), modified_after=None, k=top_k, cap=settings.RETRIEVAL_MAX_CHUNKS_PER_DOCUMENT):
        filters = dict(user_id=user_id, model_id=provider.model_id, document_ids=document_ids,
                       file_types=file_types, modified_after=modified_after)
        vector_rows = await _vector_search(db, query_vector, candidates, **filters)
        keyword_rows = await _keyword_search(db, plan.search_query, candidates, **filters)
        return _fuse([("vector", vector_rows), ("keyword", keyword_rows)], k, cap)

    results: List[RetrievedChunk] = []
    if plan.target_document_ids:
        # The user named documents: their best passages first, then the rest of the knowledge base.
        results = await run(document_ids=plan.target_document_ids, k=max(4, top_k // 2), cap=top_k)
    if plan.file_types or plan.modified_after:
        filtered = await run(file_types=plan.file_types, modified_after=plan.modified_after)
        results += [c for c in filtered if c.chunk_id not in {r.chunk_id for r in results}]
    if len(results) < top_k:
        general = await run()
        seen = {r.chunk_id for r in results}
        results += [c for c in general if c.chunk_id not in seen][: top_k - len(results)]
    return results[:top_k]


async def document_chunks_in_order(db: AsyncSession, user_id: str, document_id: str, max_chars: int) -> List[RetrievedChunk]:
    """A document's chunks in reading order; long documents are sampled evenly to fit the budget."""
    stmt = _base(user_id, get_embedding_provider().model_id, [document_id], (), None).order_by(DocumentChunk.chunk_index)
    chunks = [_to_chunk(row) for row in (await db.execute(stmt)).all()]
    total = sum(len(c.content) for c in chunks)
    if total <= max_chars or not chunks:
        return chunks
    keep = max(1, int(len(chunks) * max_chars / total))
    step = len(chunks) / keep
    return [chunks[int(i * step)] for i in range(keep)]


async def load_catalog(db: AsyncSession, user_id: str, limit: int = 500) -> List[CatalogEntry]:
    rows = (await db.execute(
        select(Document.id, Document.filename, Document.file_type, Document.folder_path, Document.modified_date, Document.drive_type)
        .where(Document.user_id == user_id, Document.status == INDEXED)
        .order_by(Document.modified_date.desc().nullslast(), Document.filename)
        .limit(limit)
    )).all()
    return [CatalogEntry(r.id, r.filename, r.file_type, r.folder_path, r.modified_date, r.drive_type) for r in rows]
