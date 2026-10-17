import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Column, Computed, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import relationship

from app.core.config import settings
from app.db.database import Base, utcnow


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("idx_chunk_user_doc", "user_id", "document_id"),
        Index("idx_chunk_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "idx_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    # Short header (document title, section path, page/slide/sheet) embedded and searched with the content.
    context = Column(Text, nullable=True)
    embedding = Column(Vector(settings.EMBEDDING_DIMENSION), nullable=True)
    embedding_model = Column(String(255), nullable=True)
    page_number = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    section = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    search_vector = Column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(context, '') || ' ' || content)", persisted=True),
    )
    created_at = Column(DateTime(timezone=True), default=utcnow)

    document = relationship("Document", back_populates="chunks")
