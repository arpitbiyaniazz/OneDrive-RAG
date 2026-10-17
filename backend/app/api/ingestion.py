from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.models.chunk import DocumentChunk
from app.models.document import DELETED, INDEXED, Document
from app.models.ingestion_job import INGEST, PENDING, PROCESSING, SYNC, IngestionJob
from app.models.user import User
from app.services import jobs

router = APIRouter(tags=["Documents & Ingestion"])


class SelectedItem(BaseModel):
    id: str = Field(..., min_length=1, max_length=255)
    drive_type: Literal["onedrive", "google_drive"]
    is_folder: bool = False
    name: Optional[str] = Field(None, max_length=1000)


class IngestRequest(BaseModel):
    items: List[SelectedItem] = Field(..., min_length=1, max_length=settings.MAX_FILES_PER_JOB)


def _job_json(job: IngestionJob) -> dict:
    total = job.total_files or 0
    done = (job.processed_files or 0) + (job.failed_files or 0)
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "total_files": total,
        "processed_files": job.processed_files or 0,
        "failed_files": job.failed_files or 0,
        "total_chunks": job.total_chunks or 0,
        "progress_percent": int(done * 100 / total) if total else (100 if job.status not in (PENDING, PROCESSING) else 0),
        "result": job.result,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.post("/ingestion/start", status_code=202)
async def start_ingestion(req: IngestRequest, user: User = Depends(get_current_user)):
    """Queues indexing of the selected files and folders (folders are expanded recursively)."""
    unique = {(i.drive_type, i.id): i.model_dump() for i in req.items}
    job = await jobs.enqueue(user.id, INGEST, {"items": list(unique.values())},
                             total_files=sum(1 for i in unique.values() if not i["is_folder"]))
    return _job_json(job)


@router.get("/ingestion/{job_id}")
async def ingestion_status(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    job = await db.get(IngestionJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_json(job)


@router.post("/sync/start", status_code=202)
async def start_sync(user: User = Depends(get_current_user)):
    """Queues a re-check of every indexed document (changed files are re-indexed, deleted ones purged)."""
    return _job_json(await jobs.enqueue(user.id, SYNC, {}))


@router.get("/sync/status")
async def sync_status(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    job = (await db.execute(
        select(IngestionJob).where(IngestionJob.user_id == user.id, IngestionJob.job_type == SYNC)
        .order_by(IngestionJob.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    return {"last_sync": _job_json(job) if job else None}


@router.get("/documents")
async def list_documents(
    drive_type: Optional[str] = Query(None, max_length=20),
    file_type: Optional[str] = Query(None, max_length=20),
    status: Optional[str] = Query(None, max_length=20),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Document).where(Document.user_id == user.id)
    stmt = stmt.where(Document.status == status.upper()) if status else stmt.where(Document.status != DELETED)
    if drive_type:
        stmt = stmt.where(Document.drive_type == drive_type)
    if file_type:
        stmt = stmt.where(Document.file_type == file_type.lower())
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    docs = (await db.execute(stmt.order_by(Document.updated_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {
        "total": total,
        "documents": [
            {
                "id": d.id,
                "filename": d.filename,
                "file_type": d.file_type,
                "drive_type": d.drive_type,
                "folder_path": d.folder_path,
                "file_size": d.file_size,
                "modified_date": d.modified_date.isoformat() if d.modified_date else None,
                "url": d.onedrive_url,
                "status": d.status,
                "chunk_count": d.chunk_count,
                "error_message": d.error_message,
                "last_ingested_at": d.last_ingested_at.isoformat() if d.last_ingested_at else None,
            }
            for d in docs
        ],
    }


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Removes a document and its chunks from the knowledge base (the file in the drive is untouched)."""
    doc = await db.get(Document, document_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    await db.execute(delete(Document).where(Document.id == doc.id))
    await db.commit()
    return {"status": "deleted", "document_id": document_id}


@router.get("/documents/stats")
async def stats(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    by_status = dict((await db.execute(
        select(Document.status, func.count()).where(Document.user_id == user.id).group_by(Document.status)
    )).all())
    chunks = (await db.execute(select(func.count(DocumentChunk.id)).where(DocumentChunk.user_id == user.id))).scalar_one()
    last = (await db.execute(select(func.max(Document.last_ingested_at)).where(Document.user_id == user.id))).scalar()
    return {
        "total_documents": by_status.get(INDEXED, 0),
        "by_status": by_status,
        "total_chunks": chunks,
        "last_sync": last.isoformat() if last else None,
    }
