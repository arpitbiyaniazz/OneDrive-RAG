import uuid

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.database import Base, utcnow

# Document.status values
PENDING = "PENDING"
INDEXED = "INDEXED"
FAILED = "FAILED"
UNSUPPORTED = "UNSUPPORTED"
DELETED = "DELETED"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("uq_doc_user_drive_file", "user_id", "drive_type", "onedrive_file_id", unique=True),
        Index("idx_doc_user_folder", "user_id", "folder_path"),
        Index("idx_doc_user_modified", "user_id", "modified_date"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Provider file id (OneDrive item id or Google Drive file id).
    onedrive_file_id = Column(String(255), nullable=False, index=True)
    drive_type = Column(String(50), default="onedrive", nullable=False, index=True)  # onedrive | google_drive
    filename = Column(String(1000), nullable=False, index=True)
    file_type = Column(String(50), nullable=False, index=True)  # pdf, docx, xlsx, pptx, txt, md, csv
    folder_path = Column(String(2000), nullable=False, default="/")  # folder relative to the app root folder
    mime_type = Column(String(255), nullable=True)
    file_size = Column(BigInteger, nullable=True)
    created_date = Column(DateTime(timezone=True), nullable=True)
    modified_date = Column(DateTime(timezone=True), nullable=True, index=True)
    # Link to the file in OneDrive / Google Drive.
    onedrive_url = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)
    # Parser/chunker/embedding-model fingerprint; a mismatch makes sync re-index the file.
    index_version = Column(String(255), nullable=True)
    chunk_count = Column(Integer, default=0, nullable=False, server_default="0")
    status = Column(String(50), default=PENDING, nullable=False, index=True)
    error_message = Column(Text, nullable=True)
    last_ingested_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan", passive_deletes=True)
