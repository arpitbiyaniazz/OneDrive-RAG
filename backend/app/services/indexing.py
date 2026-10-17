"""Parse -> chunk -> embed -> store. Shared by ingestion and sync jobs."""
import asyncio
import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.db.database import AsyncSessionLocal, utcnow
from app.models.chunk import DocumentChunk
from app.models.document import DELETED, FAILED, INDEXED, UNSUPPORTED, Document
from app.processors import UnsupportedDocumentError, parse_document
from app.services.chunker import chunk_document
from app.services.connectors import DriveItem
from app.services.embedding_service import get_embedding_provider, index_version

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    status: str  # INDEXED | UNCHANGED | UNSUPPORTED | FAILED
    chunks: int = 0
    reason: Optional[str] = None


def _file_type(item: DriveItem, detected: Optional[str]) -> str:
    if detected:
        return detected
    ext = os.path.splitext(item.name)[1].lower().lstrip(".")
    return (ext or "file")[:50]


async def _upsert_document(session, user_id: str, item: DriveItem, **fields) -> str:
    values = {
        "user_id": user_id,
        "onedrive_file_id": item.id,
        "drive_type": item.drive_type,
        "filename": item.name[:1000],
        "folder_path": item.folder_path[:2000],
        "mime_type": (item.mime_type or "")[:255],
        "file_size": item.size or None,
        "created_date": item.created_date,
        "modified_date": item.modified_date,
        "onedrive_url": item.web_url,
        "updated_at": utcnow(),
        **fields,
    }
    stmt = insert(Document).values(id=str(uuid.uuid4()), created_at=utcnow(), **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Document.user_id, Document.drive_type, Document.onedrive_file_id],
        set_={k: stmt.excluded[k] for k in values if k not in ("user_id", "drive_type", "onedrive_file_id")},
    ).returning(Document.id)
    return (await session.execute(stmt)).scalar_one()


async def index_file(user_id: str, item: DriveItem, data: bytes) -> IndexResult:
    content_hash = hashlib.sha256(data).hexdigest()
    version = index_version()

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(Document.id, Document.content_hash, Document.index_version, Document.status).where(
                Document.user_id == user_id,
                Document.drive_type == item.drive_type,
                Document.onedrive_file_id == item.id,
            )
        )).one_or_none()
        if existing and existing.content_hash == content_hash and existing.index_version == version and existing.status == INDEXED:
            # Same bytes, same pipeline: refresh metadata (rename/move/link) without re-embedding.
            await session.execute(update(Document).where(Document.id == existing.id).values(
                filename=item.name[:1000], folder_path=item.folder_path[:2000], onedrive_url=item.web_url,
                modified_date=item.modified_date, updated_at=utcnow(),
            ))
            await session.commit()
            return IndexResult("UNCHANGED")

    try:
        parsed = await asyncio.to_thread(parse_document, data, item.name, item.mime_type)
    except UnsupportedDocumentError as exc:
        await record_failure(user_id, item, UNSUPPORTED, exc.reason, content_hash)
        return IndexResult(UNSUPPORTED, reason=exc.reason)
    except Exception as exc:  # parser bug or corrupt file
        logger.exception("Parsing failed for %s", item.name)
        await record_failure(user_id, item, FAILED, f"Could not read the file ({type(exc).__name__})", content_hash)
        return IndexResult(FAILED, reason="Could not read the file")

    chunks = await asyncio.to_thread(chunk_document, parsed, item.name)
    if len(chunks) > settings.MAX_CHUNKS_PER_DOCUMENT:
        chunks = chunks[: settings.MAX_CHUNKS_PER_DOCUMENT]
        parsed.warnings.append(f"Only the first {settings.MAX_CHUNKS_PER_DOCUMENT} chunks were indexed")

    provider = get_embedding_provider()
    try:
        vectors = await provider.embed_documents([c.embedding_text for c in chunks])
    except Exception as exc:
        logger.exception("Embedding failed for %s", item.name)
        await record_failure(user_id, item, FAILED, f"Embedding failed: {exc}"[:500], None)
        return IndexResult(FAILED, reason="Embedding service error")

    async with AsyncSessionLocal() as session:
        doc_id = await _upsert_document(
            session, user_id, item,
            file_type=parsed.file_type,
            content_hash=content_hash,
            index_version=version,
            chunk_count=len(chunks),
            status=INDEXED,
            error_message="; ".join(parsed.warnings)[:2000] or None,
            last_ingested_at=utcnow(),
        )
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc_id))
        session.add_all(
            DocumentChunk(
                document_id=doc_id,
                user_id=user_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                context=chunk.context,
                embedding=vector,
                embedding_model=provider.model_id,
                page_number=chunk.page_start,
                page_end=chunk.page_end,
                section=chunk.section,
                metadata_json=chunk.metadata or None,
            )
            for chunk, vector in zip(chunks, vectors, strict=False)
        )
        await session.commit()
    return IndexResult(INDEXED, chunks=len(chunks))


async def record_failure(user_id: str, item: DriveItem, status: str, reason: str, content_hash: Optional[str]) -> None:
    """Keeps a visible document row explaining why the file is not searchable; drops stale chunks."""
    async with AsyncSessionLocal() as session:
        doc_id = await _upsert_document(
            session, user_id, item,
            file_type=_file_type(item, None),
            status=status,
            error_message=reason[:2000],
            content_hash=content_hash,
            index_version=None,
            chunk_count=0,
            last_ingested_at=utcnow(),
        )
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc_id))
        await session.commit()


async def mark_deleted(document_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        await session.execute(
            update(Document).where(Document.id == document_id).values(
                status=DELETED, chunk_count=0, index_version=None, updated_at=utcnow()
            )
        )
        await session.commit()


async def count_chunks(user_id: str) -> int:
    async with AsyncSessionLocal() as session:
        return (await session.execute(select(func.count(DocumentChunk.id)).where(DocumentChunk.user_id == user_id))).scalar_one()
