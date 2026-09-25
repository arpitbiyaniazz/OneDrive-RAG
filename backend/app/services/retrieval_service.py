from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.services.embedding_service import get_embedding_provider
from app.services.query_engine import QueryIntent


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    filename: str
    folder_path: str
    onedrive_url: Optional[str]
    page_number: Optional[int]
    section: Optional[str]
    content: str
    similarity_score: float
    metadata: Dict[str, Any]
    drive_type: str = "onedrive"


class RetrievalService:
    def __init__(self):
        self.embedding_provider = get_embedding_provider()

    async def retrieve(
        self,
        user_id: str,
        intent: QueryIntent,
        top_k: int = 5,
        session: Optional[AsyncSession] = None,
    ) -> List[RetrievedChunk]:
        """
        Performs metadata-filtered vector search with strict multi-tenant isolation.
        """
        # 1. Generate query embedding
        query_vector = await self.embedding_provider.embed_query(intent.semantic_query)

        # 2. Build SQL Query with metadata filters
        async def _execute_search(db_session: AsyncSession) -> List[RetrievedChunk]:
            stmt = (
                select(
                    DocumentChunk,
                    Document.filename,
                    Document.folder_path,
                    Document.onedrive_url,
                    Document.drive_type,
                    DocumentChunk.embedding.cosine_distance(query_vector).label("distance"),
                )
                .join(Document, DocumentChunk.document_id == Document.id)
                .where(DocumentChunk.user_id == user_id)
            )

            # Apply Metadata Filters
            if intent.folder_filter:
                stmt = stmt.where(Document.folder_path.ilike(f"{intent.folder_filter}%"))
            if intent.file_type_filter:
                stmt = stmt.where(Document.file_type == intent.file_type_filter.lower())
            if intent.modified_after:
                stmt = stmt.where(Document.modified_date >= intent.modified_after)

            # Order by vector cosine distance ascending (closest first)
            stmt = stmt.order_by("distance").limit(top_k)

            res = await db_session.execute(stmt)
            rows = res.all()

            results: List[RetrievedChunk] = []
            for row in rows:
                chunk, filename, folder_path, onedrive_url, drive_type, distance = row
                # Convert cosine distance to similarity score: 1 - distance
                sim_score = max(0.0, min(1.0, 1.0 - float(distance))) if distance is not None else 0.5
                results.append(
                    RetrievedChunk(
                        chunk_id=chunk.id,
                        document_id=chunk.document_id,
                        filename=filename,
                        folder_path=folder_path,
                        onedrive_url=onedrive_url,
                        page_number=chunk.page_number,
                        section=chunk.section,
                        content=chunk.content,
                        similarity_score=round(sim_score, 4),
                        metadata=chunk.metadata_json or {},
                        drive_type=drive_type or "onedrive",
                    )
                )
            return results

        if session is not None:
            return await _execute_search(session)
        else:
            async with AsyncSessionLocal() as s:
                return await _execute_search(s)


retrieval_service = RetrievalService()
