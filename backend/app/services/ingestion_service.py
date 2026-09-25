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
from app.models.user import OAuthAccount
from app.processors.factory import DocumentParserFactory
from app.services.chunker import StructureAwareChunker
from app.services.embedding_service import get_embedding_provider
from app.services.microsoft_graph import graph_service, mock_onedrive_provider
from app.services.google_drive import google_drive_service, mock_google_drive_provider, GoogleDriveService
from app.core.config import settings
from app.core.security import decrypt_token, encrypt_token

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

        async with AsyncSessionLocal() as session:
            # Set job status to PROCESSING
            job = await session.get(IngestionJob, job_id)
            if not job:
                logger.error(f"Job {job_id} not found.")
                return
            job.status = "PROCESSING"
            await session.commit()

        # Resolve user's OAuth tokens
        google_token = None
        microsoft_token = None
        async with AsyncSessionLocal() as session:
            stmt = select(OAuthAccount).where(OAuthAccount.user_id == user_id)
            res = await session.execute(stmt)
            accounts = res.scalars().all()
            for acc in accounts:
                if acc.provider == "google":
                    g_tok = decrypt_token(acc.encrypted_access_token)
                    google_token = g_tok
                    if acc.encrypted_refresh_token:
                        try:
                            r_tok = decrypt_token(acc.encrypted_refresh_token)
                            new_t = await google_drive_service.refresh_access_token(r_tok)
                            google_token = new_t.get("access_token", g_tok)
                            acc.encrypted_access_token = encrypt_token(google_token)
                            if new_t.get("refresh_token"):
                                acc.encrypted_refresh_token = encrypt_token(new_t["refresh_token"])
                            await session.commit()
                        except Exception as re:
                            logger.warning(f"Could not refresh Google token during ingestion: {re}")
                elif acc.provider == "microsoft":
                    ms_tok = decrypt_token(acc.encrypted_access_token)
                    microsoft_token = ms_tok
                    if acc.encrypted_refresh_token:
                        try:
                            r_tok = decrypt_token(acc.encrypted_refresh_token)
                            new_t = await graph_service.refresh_access_token(r_tok)
                            microsoft_token = new_t.get("access_token", ms_tok)
                            acc.encrypted_access_token = encrypt_token(microsoft_token)
                            if new_t.get("refresh_token"):
                                acc.encrypted_refresh_token = encrypt_token(new_t["refresh_token"])
                            await session.commit()
                        except Exception as re:
                            logger.warning(f"Could not refresh Microsoft token during ingestion: {re}")

        processed_count = 0
        failed_count = 0
        total_chunks_created = 0

        for item_id in item_ids:
            try:
                # 1. Fetch item metadata & file bytes from mock or real provider
                item_meta = None
                file_bytes = None
                is_gdrive = False

                # Check mock google drive provider first
                mock_g = await mock_google_drive_provider.get_item_by_id(item_id)
                if mock_g:
                    item_meta = mock_g
                    file_bytes = await mock_google_drive_provider.download_file_bytes(
                        item_id, item_meta.get("mime_type", "")
                    )
                    is_gdrive = True

                # Check mock onedrive provider
                if not file_bytes:
                    mock_ms = await mock_onedrive_provider.get_item_by_id(item_id)
                    if mock_ms:
                        item_meta = mock_ms
                        file_bytes = await mock_onedrive_provider.download_file_bytes(item_id)
                        is_gdrive = False

                # Try real Google Drive
                if not file_bytes and google_token:
                    try:
                        g_meta = await google_drive_service.get_file_metadata(google_token, item_id)
                        if g_meta and "name" in g_meta:
                            original_mime = g_meta.get("mimeType", "")
                            file_name = g_meta["name"]

                            # If it's a Google Workspace file, adjust name/mime for the exported format
                            export_info = GoogleDriveService.get_export_info(original_mime)
                            if export_info:
                                export_mime, export_ext = export_info
                                # Strip any .gdoc / .gsheet / .gslides pseudo-extension
                                base = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
                                file_name = f"{base}{export_ext}"
                                original_mime = export_mime

                            item_meta = {
                                "id": g_meta["id"],
                                "name": file_name,
                                "mime_type": original_mime,
                                "size": int(g_meta.get("size", 0)),
                                "path": f"/{file_name}",
                                "web_url": g_meta.get("webViewLink", ""),
                                "modified_date": g_meta.get("modifiedTime"),
                                "drive_type": "google_drive",
                            }
                            # Use resolved file ID (may differ from original if shortcut was resolved)
                            resolved_id = g_meta["id"]
                            file_bytes = await google_drive_service.download_file_bytes(
                                google_token, resolved_id, g_meta.get("mimeType", "")
                            )
                            is_gdrive = True
                    except Exception as ge:
                        logger.warning(f"Item {item_id} not fetched via Google Drive: {ge}")

                # Try real OneDrive
                if not file_bytes and microsoft_token:
                    try:
                        ms_meta = await graph_service.get_file_metadata(microsoft_token, item_id)
                        if ms_meta and "name" in ms_meta:
                            item_meta = ms_meta
                            file_bytes = await graph_service.download_file_bytes(microsoft_token, item_id)
                            is_gdrive = False
                    except Exception as me:
                        logger.warning(f"Item {item_id} not fetched via OneDrive: {me}")

                if not file_bytes:
                    logger.error(f"Could not download file content for item {item_id}")
                    failed_count += 1
                    continue

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
