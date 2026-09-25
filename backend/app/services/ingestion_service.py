import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.telemetry.otel_setup import trace_worker
from app.db.database import AsyncSessionLocal
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.models.ingestion_job import IngestionJob
from app.processors.factory import DocumentParserFactory
from app.services.chunker import StructureAwareChunker
from app.services.embedding_service import get_embedding_provider
from app.services.microsoft_graph import get_onedrive_service, mock_onedrive_provider
from app.services.google_drive import get_google_drive_service, mock_google_drive_provider
from app.core.config import settings

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(self):
        self.chunker = StructureAwareChunker(chunk_size=1000, chunk_overlap=150)
        self.embedding_provider = get_embedding_provider()

    async def create_job(self, user_id: str, total_files: int, folder_path: str = "/") -> IngestionJob:
        """Creates an IngestionJob record in the database."""
        async with AsyncSessionLocal() as session:
            job = IngestionJob(
                user_id=user_id,
                status="PENDING",
                folder_path=folder_path,
                total_files=total_files,
                processed_files=0,
                failed_files=0,
                total_chunks=0,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job

    @trace_worker("run_ingestion_pipeline")
    async def process_job(self, job_id: str, user_id: str, item_ids: List[str]):
        """
        Background task that downloads, parses, chunks, embeds,
        and saves documents and pgvector chunks into the database.
        """
        logger.info(f"Starting ingestion job {job_id} for user {user_id} ({len(item_ids)} files)")
        service = get_onedrive_service()

        async with AsyncSessionLocal() as session:
            # Set job status to PROCESSING
            job = await session.get(IngestionJob, job_id)
            if not job:
                logger.error(f"Job {job_id} not found.")
                return
            job.status = "PROCESSING"
            await session.commit()

        processed_count = 0
        failed_count = 0
        total_chunks_created = 0

        for item_id in item_ids:
            try:
                # 1. Fetch item metadata & file bytes
                item_meta = None
                is_gdrive = item_id.startswith("gdrive_") or item_id.startswith("file_gdrive_")

                if is_gdrive:
                    gdrive_service = get_google_drive_service()
                    if settings.DEV_MOCK_GDRIVE:
                        item_meta = await mock_google_drive_provider.get_item_by_id(item_id)
                        file_bytes = await mock_google_drive_provider.download_file_bytes(
                            item_id, item_meta.get("mime_type", "") if item_meta else ""
                        )
                    else:
                        item_meta = await gdrive_service.get_file_metadata("", item_id)
                        file_bytes = await gdrive_service.download_file_bytes("", item_id, item_meta.get("mimeType", ""))
                else:
                    if settings.DEV_MOCK_ONEDRIVE:
                        item_meta = await mock_onedrive_provider.get_item_by_id(item_id)
                        file_bytes = await mock_onedrive_provider.download_file_bytes(item_id)
                    else:
                        file_bytes = await service.download_file_bytes("", item_id)

                if not item_meta:
                    item_meta = {
                        "id": item_id,
                        "name": f"file_{item_id}.txt",
                        "mime_type": "text/plain",
                        "size": len(file_bytes),
                        "path": f"/{item_id}",
                        "web_url": "",
                        "modified_date": datetime.now(timezone.utc).isoformat(),
                    }

                filename = item_meta.get("name", "unknown")
                mime_type = item_meta.get("mime_type", "")
                folder_path = item_meta.get("path", "/")
                file_size = item_meta.get("size", len(file_bytes))
                web_url = item_meta.get("web_url", "")
                drive_type = "google_drive" if is_gdrive else "onedrive"
                ext = filename.split(".")[-1].lower() if "." in filename else "txt"

                # 2. Compute SHA-256 content hash
                content_hash = hashlib.sha256(file_bytes).hexdigest()

                async with AsyncSessionLocal() as session:
                    # Check if document already exists
                    stmt = select(Document).where(
                        Document.user_id == user_id,
                        Document.onedrive_file_id == item_id
                    )
                    res = await session.execute(stmt)
                    existing_doc = res.scalar_one_or_none()

                    if existing_doc and existing_doc.content_hash == content_hash and existing_doc.status == "INDEXED":
                        logger.info(f"Skipping unchanged document: {filename}")
                        processed_count += 1
                        stmt_c = select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == existing_doc.id)
                        c_res = await session.execute(stmt_c)
                        total_chunks_created += c_res.scalar() or 0
                        continue

                    # 3. Parse document
                    parser = DocumentParserFactory.get_parser(filename, mime_type)
                    parsed_doc = parser.parse(file_bytes, filename)

                    # 4. Chunk document
                    doc_meta_payload = {
                        "filename": filename,
                        "file_type": ext,
                        "folder_path": folder_path,
                        "onedrive_url": web_url,
                        "onedrive_file_id": item_id,
                        "drive_type": drive_type,
                    }
                    chunks = self.chunker.chunk_document(parsed_doc, doc_meta_payload)

                    # 5. Generate embeddings
                    chunk_texts = [c.content for c in chunks]
                    embeddings = await self.embedding_provider.embed_documents(chunk_texts)

                    # 6. Database Upsert
                    if existing_doc:
                        doc = existing_doc
                        # Delete existing chunks
                        await session.execute(
                            delete(DocumentChunk).where(DocumentChunk.document_id == doc.id)
                        )
                        doc.filename = filename
                        doc.file_type = ext
                        doc.folder_path = folder_path
                        doc.mime_type = mime_type
                        doc.file_size = file_size
                        doc.onedrive_url = web_url
                        doc.drive_type = drive_type
                        doc.content_hash = content_hash
                        doc.last_ingested_at = datetime.now(timezone.utc)
                        doc.status = "INDEXED"
                    else:
                        doc = Document(
                            user_id=user_id,
                            onedrive_file_id=item_id,
                            drive_type=drive_type,
                            filename=filename,
                            file_type=ext,
                            folder_path=folder_path,
                            mime_type=mime_type,
                            file_size=file_size,
                            onedrive_url=web_url,
                            content_hash=content_hash,
                            last_ingested_at=datetime.now(timezone.utc),
                            status="INDEXED",
                        )
                        session.add(doc)
                        await session.flush()  # assign doc.id

                    # Insert chunks
                    for idx, chunk in enumerate(chunks):
                        vec = embeddings[idx] if idx < len(embeddings) else None
                        db_chunk = DocumentChunk(
                            document_id=doc.id,
                            user_id=user_id,
                            chunk_index=chunk.chunk_index,
                            content=chunk.content,
                            embedding=vec,
                            page_number=chunk.page_number,
                            section=chunk.section,
                            metadata_json=chunk.metadata,
                        )
                        session.add(db_chunk)

                    await session.commit()

                    processed_count += 1
                    total_chunks_created += len(chunks)
                    logger.info(f"Successfully indexed {filename} ({len(chunks)} chunks)")

            except Exception as e:
                logger.error(f"Failed to ingest item {item_id}: {e}", exc_info=True)
                failed_count += 1

            # Update job progress after each file
            async with AsyncSessionLocal() as session:
                j = await session.get(IngestionJob, job_id)
                if j:
                    j.processed_files = processed_count
                    j.failed_files = failed_count
                    j.total_chunks = total_chunks_created
                    await session.commit()

        # Finalize job status
        async with AsyncSessionLocal() as session:
            j = await session.get(IngestionJob, job_id)
            if j:
                j.status = "COMPLETED" if failed_count == 0 else "COMPLETED_WITH_ERRORS"
                j.processed_files = processed_count
                j.failed_files = failed_count
                j.total_chunks = total_chunks_created
                await session.commit()

        logger.info(f"Ingestion job {job_id} finished: {processed_count} files, {total_chunks_created} chunks.")


ingestion_service = IngestionService()
