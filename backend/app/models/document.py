import uuid
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import relationship

from app.db.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    onedrive_file_id = Column(String(255), nullable=False, index=True)
    filename = Column(String(255), nullable=False, index=True)
    file_type = Column(String(50), nullable=False, index=True)  # pdf, docx, txt, xlsx, pptx
    folder_id = Column(String(255), nullable=True)
    folder_path = Column(String(1000), nullable=False, index=True)
    mime_type = Column(String(150), nullable=True)
    file_size = Column(BigInteger, nullable=True)
    created_date = Column(DateTime(timezone=True), nullable=True)
    modified_date = Column(DateTime(timezone=True), nullable=True, index=True)
    onedrive_url = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True, index=True)
    last_ingested_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="PENDING", nullable=False, index=True)  # PENDING, INDEXED, FAILED, DELETED

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    user = relationship("User", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_doc_user_onedrive", "user_id", "onedrive_file_id", unique=True),
        Index("idx_doc_user_folder", "user_id", "folder_path"),
        Index("idx_doc_user_modified", "user_id", "modified_date"),
    )
