import uuid

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, Text, text

from app.db.database import Base, utcnow

# Job types
INGEST = "ingest"
SYNC = "sync"

# Job statuses
PENDING = "PENDING"
PROCESSING = "PROCESSING"
COMPLETED = "COMPLETED"
COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
FAILED = "FAILED"
TERMINAL_STATUSES = {COMPLETED, COMPLETED_WITH_ERRORS, FAILED}


class IngestionJob(Base):
    """Durable background job (ingestion or sync), claimed by workers with FOR UPDATE SKIP LOCKED."""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        Index("idx_job_status_created", "status", "created_at"),
        # At most one queued or running sync per user.
        Index("uq_active_sync_per_user", "user_id", unique=True,
              postgresql_where=text("job_type = 'sync' AND status IN ('PENDING', 'PROCESSING')")),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    job_type = Column(String(20), default=INGEST, nullable=False, server_default=INGEST)
    status = Column(String(50), default=PENDING, nullable=False, index=True)
    payload = Column(JSON, nullable=True)
    folder_path = Column(String(1000), nullable=True)
    total_files = Column(Integer, default=0)
    processed_files = Column(Integer, default=0)
    failed_files = Column(Integer, default=0)
    total_chunks = Column(Integer, default=0)
    result = Column(JSON, nullable=True)  # per-file outcomes / sync summary
    error_message = Column(Text, nullable=True)
    attempts = Column(Integer, default=0, nullable=False, server_default="0")
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
