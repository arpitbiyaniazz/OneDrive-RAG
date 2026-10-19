import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String

from app.db.database import Base, utcnow


class TrackedFolder(Base):
    """A folder the user chose to index. Sync lists it again to pick up files added later."""

    __tablename__ = "tracked_folders"
    __table_args__ = (Index("uq_tracked_user_drive_item", "user_id", "drive_type", "item_id", unique=True),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    drive_type = Column(String(50), nullable=False)
    item_id = Column(String(255), nullable=False)  # provider folder id
    name = Column(String(1000), nullable=False, default="")
    path = Column(String(2000), nullable=False, default="/")  # relative to the app root folder
    created_at = Column(DateTime(timezone=True), default=utcnow)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
