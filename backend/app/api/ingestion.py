import asyncio
from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.models.ingestion_job import IngestionJob
from app.models.user import User
from app.services.ingestion_service import ingestion_service

router = APIRouter(prefix="", tags=["Document Ingestion & Catalog"])


class IngestRequest(BaseModel):
    item_ids: List[str]
    folder_path: Optional[str] = "/"
    items: Optional[List[dict]] = None
    drive_type: Optional[str] = None


@router.post("/ingestion/start")
async def start_ingestion(
    req: IngestRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """
    Initiates asynchronous ingestion for selected OneDrive or Google Drive files.
    """
    if not req.item_ids:
        raise HTTPException(status_code=400, detail="No items selected for ingestion.")

    job = await ingestion_service.create_job(
        user_id=current_user.id,
        total_files=len(req.item_ids),
        folder_path=req.folder_path or "/",
    )

    # Launch background ingestion pipeline
    background_tasks.add_task(
        ingestion_service.process_job,
        job_id=job.id,
        user_id=current_user.id,
        item_ids=req.item_ids,
    )

    return {
        "job_id": job.id,
        "status": job.status,
        "total_files": len(req.item_ids),
        "message": f"Ingestion started for {len(req.item_ids)} documents.",
    }


@router.get("/ingestion/{job_id}")
async def get_ingestion_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns real-time progress for an active or completed ingestion job.
    """
    job = await db.get(IngestionJob, job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Ingestion job not found.")

    progress_percent = (
        int((job.processed_files / job.total_files) * 100)
        if job.total_files > 0
        else 0
    )

    return {
        "job_id": job.id,
        "status": job.status,
        "total_files": job.total_files,
        "processed_files": job.processed_files,
        "failed_files": job.failed_files,
        "total_chunks": job.total_chunks,
        "progress_percent": progress_percent,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "error_message": job.error_message,
    }


@router.get("/documents")
async def list_documents(
    folder_path: Optional[str] = Query(None),
    file_type: Optional[str] = Query(None),
    drive_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lists all indexed documents for the authenticated user with optional folder, type, or drive_type filters.
    """
    stmt = select(Document).where(Document.user_id == current_user.id)
    if status:
        stmt = stmt.where(Document.status == status)
    else:
        stmt = stmt.where(Document.status != "DELETED")

    if folder_path:
        stmt = stmt.where(Document.folder_path.ilike(f"{folder_path}%"))
    if file_type:
        stmt = stmt.where(Document.file_type == file_type.lower())
    if drive_type:
        stmt = stmt.where(Document.drive_type == drive_type.lower())

    stmt = stmt.order_by(Document.created_at.desc())
    res = await db.execute(stmt)
    docs = res.scalars().all()

    return {
        "documents": [
            {
                "id": d.id,
                "filename": d.filename,
                "file_type": d.file_type,
                "drive_type": d.drive_type or "onedrive",
                "folder_path": d.folder_path,
                "file_size": d.file_size,
                "modified_date": d.modified_date.isoformat() if d.modified_date else None,
                "onedrive_url": d.onedrive_url,
                "content_hash": d.content_hash,
                "status": d.status,
                "last_ingested_at": d.last_ingested_at.isoformat() if d.last_ingested_at else None,
            }
            for d in docs
        ]
    }


@router.get("/documents/stats")
async def get_knowledge_base_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns high-level statistics for the user's Knowledge Base dashboard.
    """
    # Count total active documents
    doc_stmt = select(func.count(Document.id)).where(
        Document.user_id == current_user.id,
        Document.status != "DELETED"
    )
    doc_res = await db.execute(doc_stmt)
    total_docs = doc_res.scalar() or 0

    # Count total chunks
    chunk_stmt = select(func.count(DocumentChunk.id)).where(DocumentChunk.user_id == current_user.id)
    chunk_res = await db.execute(chunk_stmt)
    total_chunks = chunk_res.scalar() or 0

    # Get last ingested timestamp
    last_stmt = select(func.max(Document.last_ingested_at)).where(Document.user_id == current_user.id)
    last_res = await db.execute(last_stmt)
    last_sync = last_res.scalar()

    return {
        "total_documents": total_docs,
        "total_chunks": total_chunks,
        "last_sync": last_sync.isoformat() if last_sync else None,
        "status": "Healthy" if total_docs > 0 else "Empty",
    }
