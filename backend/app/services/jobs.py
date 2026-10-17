"""Durable background jobs stored in Postgres and claimed with FOR UPDATE SKIP LOCKED.

Jobs survive restarts, run on any API replica, and are re-claimed if a worker dies mid-job
(heartbeat older than WORKER_STALE_JOB_SECONDS). Each job is attempted at most MAX_ATTEMPTS times.
"""
import asyncio
import logging
from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy import or_, select, update

from app.core.config import settings
from app.core.telemetry.otel_setup import trace_worker
from app.db.database import AsyncSessionLocal, utcnow
from app.models.document import DELETED, INDEXED, UNSUPPORTED, Document
from app.models.document import FAILED as DOC_FAILED
from app.models.ingestion_job import (
    COMPLETED,
    COMPLETED_WITH_ERRORS,
    FAILED,
    INGEST,
    PENDING,
    PROCESSING,
    SYNC,
    IngestionJob,
)
from app.services.connectors import (
    ConnectorAuthError,
    ConnectorError,
    DriveItem,
    FileTooLargeError,
    NotFoundError,
    get_connector,
    walk_files,
)
from app.services.embedding_service import index_version
from app.services.indexing import index_file, mark_deleted, record_failure
from app.services.oauth_tokens import get_drive_token

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


async def enqueue(user_id: str, job_type: str, payload: dict, total_files: int = 0) -> IngestionJob:
    async with AsyncSessionLocal() as session:
        if job_type == SYNC:
            active = (await session.execute(
                select(IngestionJob).where(
                    IngestionJob.user_id == user_id,
                    IngestionJob.job_type == SYNC,
                    IngestionJob.status.in_([PENDING, PROCESSING]),
                )
            )).scalars().first()
            if active:
                return active
        job = IngestionJob(user_id=user_id, job_type=job_type, status=PENDING, payload=payload, total_files=total_files)
        session.add(job)
        await session.commit()
        return job


async def claim_next() -> Optional[IngestionJob]:
    stale_before = utcnow() - timedelta(seconds=settings.WORKER_STALE_JOB_SECONDS)
    async with AsyncSessionLocal() as session:
        job = (await session.execute(
            select(IngestionJob)
            .where(or_(
                IngestionJob.status == PENDING,
                (IngestionJob.status == PROCESSING) & (IngestionJob.heartbeat_at < stale_before),
            ))
            .order_by(IngestionJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )).scalar_one_or_none()
        if job is None:
            return None
        now = utcnow()
        if job.attempts >= MAX_ATTEMPTS:
            job.status, job.finished_at = FAILED, now
            job.error_message = "Job abandoned after repeated worker interruptions"
            await session.commit()
            return None
        job.status, job.attempts, job.heartbeat_at = PROCESSING, job.attempts + 1, now
        job.started_at = job.started_at or now
        await session.commit()
        return job


class _Progress:
    def __init__(self, job: IngestionJob):
        self.job_id = job.id
        self.processed = self.failed = self.chunks = 0
        self.total = job.total_files or 0
        self.files: List[Dict] = []
        self.summary: Dict[str, int] = defaultdict(int)

    def add(self, name: str, status: str, chunks: int = 0, reason: Optional[str] = None) -> None:
        self.files.append({"name": name, "status": status, **({"reason": reason} if reason else {})})
        self.summary[status.lower()] += 1
        if status in (DOC_FAILED, "FAILED"):
            self.failed += 1
        else:
            self.processed += 1
        self.chunks += chunks

    async def save(self, **extra) -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(update(IngestionJob).where(IngestionJob.id == self.job_id).values(
                processed_files=self.processed,
                failed_files=self.failed,
                total_chunks=self.chunks,
                total_files=max(self.total, self.processed + self.failed),
                heartbeat_at=utcnow(),
                result={"files": self.files[-500:], "summary": dict(self.summary)},
                **extra,
            ))
            await session.commit()


async def _token(user_id: str, drive_type: str) -> str:
    async with AsyncSessionLocal() as session:
        return await get_drive_token(session, user_id, drive_type)


async def _process_file(user_id: str, connector, token: str, item: DriveItem, progress: _Progress) -> None:
    if not item.supported:
        reason = "Unsupported file type"
        await record_failure(user_id, item, UNSUPPORTED, reason, None)
        progress.add(item.name, UNSUPPORTED, reason=reason)
        return
    if item.size and item.size > settings.max_file_size_bytes:
        reason = f"File exceeds the {settings.MAX_FILE_SIZE_MB} MB limit"
        await record_failure(user_id, item, UNSUPPORTED, reason, None)
        progress.add(item.name, UNSUPPORTED, reason=reason)
        return
    try:
        data = await connector.download(token, item)
    except FileTooLargeError as exc:
        await record_failure(user_id, item, UNSUPPORTED, str(exc), None)
        progress.add(item.name, UNSUPPORTED, reason=str(exc))
        return
    result = await index_file(user_id, item, data)
    progress.add(item.name, result.status, result.chunks, result.reason)


@trace_worker("ingest")
async def run_ingest(job: IngestionJob) -> None:
    progress = _Progress(job)
    by_drive: Dict[str, List[dict]] = defaultdict(list)
    for selection in (job.payload or {}).get("items", []):
        by_drive[selection["drive_type"]].append(selection)

    for drive_type, selections in by_drive.items():
        connector = get_connector(drive_type)
        try:
            token = await _token(job.user_id, drive_type)
            await connector.get_root(token)  # validates the connection and caches the root id
        except ConnectorError as exc:
            for s in selections:
                progress.add(s.get("name") or s["id"], "FAILED", reason=str(exc))
            continue

        files: Dict[str, DriveItem] = {}
        for selection in selections:
            try:
                item = await connector.get_file(token, selection["id"])
                if item.is_folder:
                    remaining = settings.MAX_FILES_PER_JOB - len(files)
                    for f in await walk_files(connector, token, item.id, item.path, remaining):
                        files[f.id] = f
                else:
                    files[item.id] = item
            except ConnectorError as exc:
                progress.add(selection.get("name") or selection["id"], "FAILED", reason=str(exc))
            if len(files) >= settings.MAX_FILES_PER_JOB:
                break
        progress.total += len(files)
        await progress.save()

        for item in files.values():
            try:
                await _process_file(job.user_id, connector, token, item, progress)
            except ConnectorAuthError as exc:
                progress.add(item.name, "FAILED", reason=str(exc))
                break
            except ConnectorError as exc:
                progress.add(item.name, "FAILED", reason=str(exc))
            except Exception:
                logger.exception("Unexpected error indexing %s", item.name)
                progress.add(item.name, "FAILED", reason="Unexpected error")
            await progress.save()
    await _finish(progress)


@trace_worker("sync")
async def run_sync(job: IngestionJob) -> None:
    """Re-checks every indexed document: re-index changed files, purge deleted ones."""
    progress = _Progress(job)
    current_version = index_version()
    async with AsyncSessionLocal() as session:
        docs = (await session.execute(
            select(Document).where(Document.user_id == job.user_id, Document.status != DELETED)
        )).scalars().all()
    progress.total = len(docs)
    await progress.save()

    by_drive: Dict[str, List[Document]] = defaultdict(list)
    for doc in docs:
        by_drive[doc.drive_type].append(doc)

    for drive_type, drive_docs in by_drive.items():
        connector = get_connector(drive_type)
        try:
            token = await _token(job.user_id, drive_type)
            await connector.get_root(token)
        except ConnectorError as exc:
            # Never delete anything when the drive cannot be listed: that is not evidence of deletion.
            for doc in drive_docs:
                progress.add(doc.filename, "FAILED", reason=str(exc))
            continue
        for doc in drive_docs:
            try:
                item = await connector.get_file(token, doc.onedrive_file_id)
            except NotFoundError:
                await mark_deleted(doc.id)
                progress.add(doc.filename, DELETED)
                await progress.save()
                continue
            except ConnectorError as exc:
                progress.add(doc.filename, "FAILED", reason=str(exc))
                continue
            same_file = (
                doc.filename == item.name
                and doc.folder_path == item.folder_path
                and doc.modified_date is not None
                and item.modified_date is not None
                and doc.modified_date >= item.modified_date
            )
            up_to_date = doc.status == UNSUPPORTED or (doc.status == INDEXED and doc.index_version == current_version)
            if same_file and up_to_date:
                progress.add(doc.filename, "UNCHANGED")
                continue
            try:
                await _process_file(job.user_id, connector, token, item, progress)
            except ConnectorError as exc:
                progress.add(doc.filename, "FAILED", reason=str(exc))
            await progress.save()
    await _finish(progress)


async def _finish(progress: _Progress) -> None:
    status = COMPLETED if progress.failed == 0 else (FAILED if progress.processed == 0 else COMPLETED_WITH_ERRORS)
    error = None
    if progress.failed:
        reasons = sorted({f.get("reason") for f in progress.files if f["status"] == "FAILED" and f.get("reason")})
        error = "; ".join(reasons)[:1000] or None
    await progress.save(status=status, finished_at=utcnow(), error_message=error)


async def run_job(job: IngestionJob) -> None:
    try:
        if job.job_type == SYNC:
            await run_sync(job)
        else:
            await run_ingest(job)
    except Exception as exc:
        logger.exception("Job %s failed", job.id)
        async with AsyncSessionLocal() as session:
            await session.execute(update(IngestionJob).where(IngestionJob.id == job.id).values(
                status=FAILED, finished_at=utcnow(), error_message=f"Internal error: {type(exc).__name__}",
            ))
            await session.commit()


class Worker:
    """Polls for jobs inside the API process. Several replicas can run it concurrently."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="job-worker")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def run_once(self) -> bool:
        job = await claim_next()
        if job is None:
            return False
        await run_job(job)
        return True

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                if await self.run_once():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker loop error")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=settings.WORKER_POLL_SECONDS)
            except TimeoutError:
                pass


worker = Worker()


async def drain_jobs(max_jobs: int = 50) -> int:
    """Runs queued jobs synchronously until none are left (tests and CLI)."""
    count = 0
    while count < max_jobs and await worker.run_once():
        count += 1
    return count


__all__ = ["INGEST", "SYNC", "enqueue", "drain_jobs", "worker"]
