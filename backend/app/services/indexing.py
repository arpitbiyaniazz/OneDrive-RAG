"""Parse -> (OCR) -> chunk -> embed -> store. Shared by ingestion and sync jobs."""
import asyncio
import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.db.database import AsyncSessionLocal, utcnow
from app.models.chunk import DocumentChunk
from app.models.document import DELETED, FAILED, INDEXED, UNSUPPORTED, Document
from app.processors import ParsedDocument, UnsupportedDocumentError, parse_document
from app.services.chunker import chunk_document
from app.services.connectors import DriveItem
from app.services.embedding_service import get_embedding_provider, index_version, unsupported_version
from app.services.ocr_service import OCRError, ocr_document, ocr_enabled, ocr_scanned_pages

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


def _key(item: DriveItem, user_id: str):
    return (Document.user_id == user_id, Document.drive_type == item.drive_type, Document.onedrive_file_id == item.id)


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


async def _rename_contexts(session, document_id: str, old_name: str, new_name: str) -> None:
    """Chunk headers start with "Document: <name>"; keep them (and the full-text index) in step with renames."""
    old_prefix, new_prefix = f"Document: {old_name}", f"Document: {new_name}"
    await session.execute(
        update(DocumentChunk)
        .where(DocumentChunk.document_id == document_id, func.starts_with(DocumentChunk.context, old_prefix))
        .values(context=literal(new_prefix) + func.substr(DocumentChunk.context, len(old_prefix) + 1))
    )


async def _parse(data: bytes, item: DriveItem) -> ParsedDocument:
    """Structured text of the file; scans and images go through OCR when it is enabled."""
    try:
        parsed = await asyncio.wait_for(asyncio.to_thread(parse_document, data, item.name, item.mime_type),
                                        timeout=settings.PARSE_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise UnsupportedDocumentError("The file took too long to read") from exc
    except UnsupportedDocumentError as exc:
        if not (exc.needs_ocr and ocr_enabled()):
            raise
        return await ocr_document(data, exc.file_type or "pdf")
    if parsed.ocr_pages:
        if ocr_enabled():
            try:
                parsed = await ocr_scanned_pages(data, parsed)
            except OCRError as exc:
                if exc.permanent:
                    parsed.warnings.append(f"Scanned pages could not be read: {exc}")
                else:
                    raise
        else:
            pages = ", ".join(str(n) for n in parsed.ocr_pages[:20])
            parsed.warnings.append(f"Scanned pages were skipped because OCR is turned off (pages {pages})")
    return parsed


async def index_file(user_id: str, item: DriveItem, data: bytes) -> IndexResult:
    content_hash = hashlib.sha256(data).hexdigest()
    version = index_version()

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(Document.id, Document.filename, Document.content_hash, Document.index_version, Document.status)
            .where(*_key(item, user_id))
        )).one_or_none()
        if existing and existing.content_hash == content_hash and existing.index_version == version and existing.status == INDEXED:
            # Same bytes, same pipeline: refresh metadata (rename/move/link) without re-embedding.
            if existing.filename != item.name:
                await _rename_contexts(session, existing.id, existing.filename, item.name)
            await session.execute(update(Document).where(Document.id == existing.id).values(
                filename=item.name[:1000], folder_path=item.folder_path[:2000], onedrive_url=item.web_url,
                modified_date=item.modified_date, file_size=item.size or None, error_message=None, updated_at=utcnow(),
            ))
            await session.commit()
            return IndexResult("UNCHANGED")
    keep_previous = existing is not None and existing.status == INDEXED

    try:
        parsed = await _parse(data, item)
    except UnsupportedDocumentError as exc:
        await record_failure(user_id, item, UNSUPPORTED, exc.reason, content_hash, unsupported_version())
        return IndexResult(UNSUPPORTED, reason=exc.reason)
    except OCRError as exc:
        if exc.permanent:
            reason = f"OCR could not read the file: {exc}"
            await record_failure(user_id, item, UNSUPPORTED, reason, content_hash, unsupported_version())
            return IndexResult(UNSUPPORTED, reason=reason)
        logger.warning("OCR failed for %s: %s", item.name, exc)
        return await _failed(user_id, item, keep_previous, f"OCR failed: {exc}")
    except Exception as exc:  # parser bug or corrupt file
        logger.exception("Parsing failed for %s", item.name)
        return await _failed(user_id, item, keep_previous, f"Could not read the file ({type(exc).__name__})")

    chunks = await asyncio.to_thread(chunk_document, parsed, item.name)
    cap = settings.MAX_CHUNKS_PER_DOCUMENT
    if len(chunks) > cap:
        total = len(chunks)
        if parsed.file_type == "txt":  # logs grow at the end: keep the beginning and the newest entries
            head = cap // 5
            chunks = chunks[:head] + chunks[-(cap - head):]
            parsed.warnings.append(f"Only {cap} of {total} passages were indexed (the start and the end)")
        else:
            chunks = chunks[:cap]
            parsed.warnings.append(f"Only the first {cap} passages were indexed")

    provider = get_embedding_provider()
    try:
        vectors = await provider.embed_documents([c.embedding_text for c in chunks])
    except Exception as exc:
        logger.exception("Embedding failed for %s", item.name)
        return await _failed(user_id, item, keep_previous, f"Embedding service error: {exc}"[:300])

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
            for chunk, vector in zip(chunks, vectors, strict=True)
        )
        await session.commit()
    return IndexResult(INDEXED, chunks=len(chunks))


async def _failed(user_id: str, item: DriveItem, keep_previous: bool, reason: str) -> IndexResult:
    """A transient failure. An already indexed document keeps serving its last good version; its
    stored hash and dates are left as they were, so the next sync tries again."""
    if keep_previous:
        async with AsyncSessionLocal() as session:
            await session.execute(update(Document).where(*_key(item, user_id)).values(
                error_message=f"The latest version could not be indexed ({reason}); answers use the previous version."[:2000],
                updated_at=utcnow(),
            ))
            await session.commit()
    else:
        await record_failure(user_id, item, FAILED, reason, None, None)
    return IndexResult(FAILED, reason=reason)


async def record_failure(user_id: str, item: DriveItem, status: str, reason: str, content_hash: Optional[str],
                         version: Optional[str] = None) -> None:
    """Keeps a visible document row explaining why the file is not searchable; drops stale chunks."""
    async with AsyncSessionLocal() as session:
        doc_id = await _upsert_document(
            session, user_id, item,
            file_type=_file_type(item, None),
            status=status,
            error_message=reason[:2000],
            content_hash=content_hash,
            index_version=version,
            chunk_count=0,
            last_ingested_at=utcnow(),
        )
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc_id))
        await session.commit()


async def mark_deleted(document_id: str, status: str = DELETED) -> None:
    """Drops a document's passages; the row stays (DELETED: gone from the drive, REMOVED: by the user)."""
    async with AsyncSessionLocal() as session:
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        await session.execute(
            update(Document).where(Document.id == document_id).values(
                status=status, chunk_count=0, index_version=None, content_hash=None, error_message=None,
                updated_at=utcnow(),
            )
        )
        await session.commit()


async def count_chunks(user_id: str) -> int:
    async with AsyncSessionLocal() as session:
        return (await session.execute(select(func.count(DocumentChunk.id)).where(DocumentChunk.user_id == user_id))).scalar_one()
