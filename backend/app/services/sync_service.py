import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from sqlalchemy import select, delete

from app.core.telemetry.otel_setup import trace_worker
from app.db.database import AsyncSessionLocal
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.processors.factory import DocumentParserFactory
from app.services.chunker import StructureAwareChunker
from app.services.embedding_service import get_embedding_provider
from app.services.microsoft_graph import get_onedrive_service, mock_onedrive_provider
from app.core.config import settings

logger = logging.getLogger(__name__)


class SyncResult:
    def __init__(self):
        self.unchanged_files: int = 0
        self.updated_files: int = 0
        self.new_files: int = 0
        self.deleted_files: int = 0
        self.total_scanned: int = 0
        self.duration_seconds: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "unchanged_files": self.unchanged_files,
            "updated_files": self.updated_files,
            "new_files": self.new_files,
            "deleted_files": self.deleted_files,
            "total_scanned": self.total_scanned,
            "duration_seconds": round(self.duration_seconds, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class IncrementalSyncService:
    """
    Synchronizes connected OneDrive files with the pgvector knowledge base.
    Skips unchanged files, updates modified files, and purges deleted documents.
    """

    def __init__(self):
        self.chunker = StructureAwareChunker(chunk_size=1000, chunk_overlap=150)
        self.embedding_provider = get_embedding_provider()
        self._last_sync_result: Optional[SyncResult] = None
        self.is_syncing: bool = False

    @trace_worker("incremental_onedrive_sync")
    async def run_sync(self, user_id: str) -> SyncResult:
        if self.is_syncing:
            logger.warning(f"Sync already running. Skipping duplicate call for {user_id}")
            if self._last_sync_result:
                return self._last_sync_result
            return SyncResult()

        self.is_syncing = True
        start_t = time.perf_counter()
        result = SyncResult()
        service = get_onedrive_service()

        try:
            logger.info(f"Starting incremental sync for user {user_id}")

            # 1. Fetch all items currently in OneDrive
            remote_items: List[Dict] = []
            if settings.DEV_MOCK_ONEDRIVE:
                # Flatten mock tree
                for folder_id in ["root", "folder_hr", "folder_finance", "folder_engineering"]:
                    items = await mock_onedrive_provider.list_drive_items(folder_id)
                    for it in items:
                        if not it.get("is_folder"):
                            remote_items.append(it)
            else:
                # Production: fetch recursive root items
                pass

            result.total_scanned = len(remote_items)
            remote_ids = {it["id"] for it in remote_items}

            # 2. Fetch existing DB documents for this user (OneDrive only)
            async with AsyncSessionLocal() as session:
                stmt = select(Document).where(
                    Document.user_id == user_id,
                    Document.status != "DELETED",
                    Document.drive_type == "onedrive",
                )
                res = await session.execute(stmt)
                db_docs = res.scalars().all()
                db_doc_map = {d.onedrive_file_id: d for d in db_docs}

            # 3. Check for deleted files
            for onedrive_id, db_doc in db_doc_map.items():
                if onedrive_id not in remote_ids:
                    async with AsyncSessionLocal() as session:
                        doc = await session.get(Document, db_doc.id)
                        if doc:
                            doc.status = "DELETED"
                            await session.execute(
                                delete(DocumentChunk).where(DocumentChunk.document_id == doc.id)
                            )
                            await session.commit()
                            result.deleted_files += 1
                            logger.info(f"Detected deleted file: {doc.filename}. Purged pgvector chunks.")

            # 4. Check remote files for changes or new additions
            for item in remote_items:
                item_id = item["id"]
                filename = item["name"]
                path = item.get("path", "/")
                mime_type = item.get("mime_type", "")
                ext = filename.split(".")[-1].lower() if "." in filename else "txt"

                # Download file bytes
                if settings.DEV_MOCK_ONEDRIVE:
                    file_bytes = await mock_onedrive_provider.download_file_bytes(item_id)
                else:
                    file_bytes = await service.download_file_bytes("", item_id)

                content_hash = hashlib.sha256(file_bytes).hexdigest()

                db_doc = db_doc_map.get(item_id)

                if db_doc and db_doc.content_hash == content_hash:
                    result.unchanged_files += 1
                    continue

                # File is either new or modified
                is_new = db_doc is None

                # Parse & Chunk
                parser = DocumentParserFactory.get_parser(filename, mime_type)
                parsed_doc = parser.parse(file_bytes, filename)
                doc_meta_payload = {
                    "filename": filename,
                    "file_type": ext,
                    "folder_path": path,
                    "onedrive_url": item.get("web_url", ""),
                    "onedrive_file_id": item_id,
                }
                chunks = self.chunker.chunk_document(parsed_doc, doc_meta_payload)
                embeddings = await self.embedding_provider.embed_documents([c.content for c in chunks])

                async with AsyncSessionLocal() as session:
                    if db_doc:
                        # Update existing
                        doc = await session.get(Document, db_doc.id)
                        await session.execute(
                            delete(DocumentChunk).where(DocumentChunk.document_id == doc.id)
                        )
                        doc.content_hash = content_hash
                        doc.last_ingested_at = datetime.now(timezone.utc)
                        doc.status = "INDEXED"
                        result.updated_files += 1
                        logger.info(f"Updated modified file: {filename}")
                    else:
                        # New document
                        doc = Document(
                            user_id=user_id,
                            onedrive_file_id=item_id,
                            filename=filename,
                            file_type=ext,
                            folder_path=path,
                            mime_type=mime_type,
                            file_size=len(file_bytes),
                            onedrive_url=item.get("web_url", ""),
                            content_hash=content_hash,
                            last_ingested_at=datetime.now(timezone.utc),
                            status="INDEXED",
                        )
                        session.add(doc)
                        await session.flush()
                        result.new_files += 1
                        logger.info(f"Indexed newly discovered file: {filename}")

                    # Insert new chunks
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
        finally:
            self.is_syncing = False

        result.duration_seconds = time.perf_counter() - start_t
        self._last_sync_result = result
        return result

    def get_last_result(self) -> Optional[Dict]:
        return self._last_sync_result.to_dict() if self._last_sync_result else None


sync_service = IncrementalSyncService()
