"""Durable background jobs stored in Postgres and claimed with FOR UPDATE SKIP LOCKED.

Jobs survive restarts, run on any API replica, and are re-claimed if a worker dies mid-job
(heartbeat older than WORKER_STALE_JOB_SECONDS). A running job sends a heartbeat in the background,
and a worker that shuts down hands its job back at once. Each job is attempted at most MAX_ATTEMPTS
times.
"""
import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.core.telemetry.otel_setup import trace_worker
from app.db.database import AsyncSessionLocal, utcnow
from app.models.document import DELETED, INDEXED, REMOVED, UNSUPPORTED, Document
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
from app.models.tracked_folder import TrackedFolder
from app.services.connectors import (
    Connector,
    ConnectorAuthError,
    ConnectorError,
    DriveItem,
    FileTooLargeError,
    NotFoundError,
    get_connector,
    walk_files,
)
from app.services.embedding_service import index_version, unsupported_version
from app.services.indexing import index_file, mark_deleted, record_failure
from app.services.oauth_tokens import get_drive_token

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


class TooManyJobsError(Exception):
    pass


class JobLostError(Exception):
    """Another worker re-claimed this job (it looked abandoned); this run must stop writing."""


async def enqueue(user_id: str, job_type: str, payload: dict, total_files: int = 0, _retry: bool = True) -> IngestionJob:
    async with AsyncSessionLocal() as session:
        active = select(IngestionJob).where(
            IngestionJob.user_id == user_id, IngestionJob.status.in_([PENDING, PROCESSING])
        )
        if job_type == SYNC:
            running = (await session.execute(active.where(IngestionJob.job_type == SYNC))).scalars().first()
            if running:
                return running
        else:
            count = (await session.execute(
                select(func.count()).select_from(active.where(IngestionJob.job_type == INGEST).subquery())
            )).scalar_one()
            if count >= settings.MAX_ACTIVE_JOBS_PER_USER:
                raise TooManyJobsError("Earlier indexing requests are still running. Try again when they finish.")
        job = IngestionJob(user_id=user_id, job_type=job_type, status=PENDING, payload=payload, total_files=total_files)
        session.add(job)
        try:
            await session.commit()
        except IntegrityError:  # a concurrent request queued the user's sync first (uq_active_sync_per_user)
            await session.rollback()
            if job_type != SYNC:
                raise
            running = (await session.execute(active.where(IngestionJob.job_type == SYNC))).scalars().first()
            if running is None and _retry:  # that sync finished in between
                return await enqueue(user_id, job_type, payload, total_files, _retry=False)
            if running is None:
                raise
            return running
        return job


async def claim_next() -> Optional[IngestionJob]:
    """Oldest runnable job of a user who has no other job running (jobs of one user never overlap,
    and one user's long job does not hold up the others)."""
    stale_before = utcnow() - timedelta(seconds=settings.WORKER_STALE_JOB_SECONDS)
    running = aliased(IngestionJob)
    user_busy = select(running.id).where(
        running.user_id == IngestionJob.user_id,
        running.id != IngestionJob.id,
        running.status == PROCESSING,
        running.heartbeat_at >= stale_before,
    ).exists()
    async with AsyncSessionLocal() as session:
        job = (await session.execute(
            select(IngestionJob)
            .where(or_(
                IngestionJob.status == PENDING,
                (IngestionJob.status == PROCESSING) & (IngestionJob.heartbeat_at < stale_before),
            ))
            .where(~user_busy)
            .order_by(IngestionJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True, of=IngestionJob)
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


def _owned(job_id: str, attempt: int):
    """WHERE clause for writes by the worker holding `attempt`: a re-claimed job ignores stale runs."""
    return (IngestionJob.id == job_id, IngestionJob.attempts == attempt)


class _Progress:
    def __init__(self, job: IngestionJob):
        self.job_id = job.id
        self.attempt = job.attempts
        self.processed = self.failed = self.chunks = 0
        self.total = job.total_files or 0
        self.files: List[Dict] = []
        self.summary: Dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    def add(self, name: str, status: str, chunks: int = 0, reason: Optional[str] = None) -> None:
        self.files.append({"name": name, "status": status, **({"reason": reason} if reason else {})})
        self.summary[status.lower()] += 1
        if status == "FAILED":
            self.failed += 1
        else:
            self.processed += 1
        self.chunks += chunks

    async def save(self, **extra) -> None:
        async with self._lock:  # concurrent file tasks must not commit an older snapshot last
            async with AsyncSessionLocal() as session:
                result = await session.execute(update(IngestionJob).where(*_owned(self.job_id, self.attempt)).values(
                    processed_files=self.processed,
                    failed_files=self.failed,
                    total_chunks=self.chunks,
                    total_files=max(self.total, self.processed + self.failed),
                    heartbeat_at=utcnow(),
                    result={"files": self.files[-500:], "summary": dict(self.summary)},
                    **extra,
                ))
                await session.commit()
        if result.rowcount == 0:
            raise JobLostError(self.job_id)


class _Tokens:
    """A valid access token on every call: long jobs outlive a single token, and get_drive_token
    refreshes only when the current one is about to expire."""

    def __init__(self, user_id: str, drive_type: str):
        self.user_id, self.drive_type = user_id, drive_type

    async def get(self) -> str:
        async with AsyncSessionLocal() as session:
            return await get_drive_token(session, self.user_id, self.drive_type)


async def _process_file(user_id: str, connector: Connector, token: str, item: DriveItem, progress: _Progress) -> None:
    if not item.supported:
        reason = "Unsupported file type"
        await record_failure(user_id, item, UNSUPPORTED, reason, None, unsupported_version())
        progress.add(item.name, UNSUPPORTED, reason=reason)
        return
    if item.size and item.size > settings.max_file_size_bytes:
        reason = f"File exceeds the {settings.MAX_FILE_SIZE_MB} MB limit"
        await record_failure(user_id, item, UNSUPPORTED, reason, None, unsupported_version())
        progress.add(item.name, UNSUPPORTED, reason=reason)
        return
    try:
        data = await connector.download(token, item)
    except FileTooLargeError as exc:
        await record_failure(user_id, item, UNSUPPORTED, str(exc), None, unsupported_version())
        progress.add(item.name, UNSUPPORTED, reason=str(exc))
        return
    result = await index_file(user_id, item, data)
    progress.add(item.name, result.status, result.chunks, result.reason)


async def _process_files(user_id: str, connector: Connector, tokens: _Tokens, items: List[DriveItem],
                         progress: _Progress) -> None:
    """Indexes files with bounded concurrency. A revoked grant stops the rest of this drive."""
    gate = asyncio.Semaphore(max(1, settings.INGEST_CONCURRENCY))
    auth_error: List[str] = []

    async def one(item: DriveItem) -> None:
        async with gate:
            if auth_error:
                progress.add(item.name, "FAILED", reason=auth_error[0])
            else:
                try:
                    await _process_file(user_id, connector, await tokens.get(), item, progress)
                except ConnectorAuthError as exc:
                    auth_error.append(str(exc))
                    progress.add(item.name, "FAILED", reason=str(exc))
                except ConnectorError as exc:
                    progress.add(item.name, "FAILED", reason=str(exc))
                except Exception:
                    logger.exception("Unexpected error indexing %s", item.name)
                    progress.add(item.name, "FAILED", reason="Unexpected error")
            await progress.save()

    # A task group cancels the remaining files when this run loses the job (JobLostError).
    try:
        async with asyncio.TaskGroup() as group:
            for item in items:
                group.create_task(one(item))
    except BaseExceptionGroup as errors:
        lost = [e for e in errors.exceptions if isinstance(e, JobLostError)]
        if lost:
            raise lost[0] from None
        raise


async def track_folder(user_id: str, folder: DriveItem) -> None:
    """Remembers a folder the user indexed so later syncs pick up files added to it."""
    now = utcnow()
    values = {"name": folder.name[:1000], "path": folder.path[:2000], "last_synced_at": now}
    stmt = insert(TrackedFolder).values(id=str(uuid.uuid4()), user_id=user_id, drive_type=folder.drive_type,
                                        item_id=folder.id, created_at=now, **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[TrackedFolder.user_id, TrackedFolder.drive_type, TrackedFolder.item_id], set_=values
    )
    async with AsyncSessionLocal() as session:
        await session.execute(stmt)
        await session.commit()


async def _untrack(folder_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(TrackedFolder).where(TrackedFolder.id == folder_id))
        await session.commit()


async def _connect(user_id: str, drive_type: str) -> Tuple[Connector, _Tokens, str]:
    connector = get_connector(drive_type)
    tokens = _Tokens(user_id, drive_type)
    token = await tokens.get()
    await connector.get_root(token)  # validates the connection and caches the root id
    return connector, tokens, token


@trace_worker("ingest")
async def run_ingest(job: IngestionJob) -> None:
    progress = _Progress(job)
    progress.total = 0  # the enqueue-time estimate counted selected files; count what is found instead
    by_drive: Dict[str, List[dict]] = defaultdict(list)
    for selection in (job.payload or {}).get("items", []):
        by_drive[selection["drive_type"]].append(selection)

    for drive_type, selections in by_drive.items():
        try:
            connector, tokens, token = await _connect(job.user_id, drive_type)
        except ConnectorError as exc:
            for s in selections:
                progress.add(s.get("name") or s["id"], "FAILED", reason=str(exc))
            continue

        files: Dict[str, DriveItem] = {}
        for selection in selections:
            try:
                item = await connector.get_file(token, selection["id"])
                if item.is_folder:
                    await track_folder(job.user_id, item)
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
        await _process_files(job.user_id, connector, tokens, list(files.values()), progress)
    await _finish(progress)


def _up_to_date(doc: Document, item: DriveItem, current: str, current_unsupported: str) -> bool:
    # Equality, not ">=": a file replaced by an older copy (sync clients keep local dates) changed too.
    same_file = (
        doc.filename == item.name
        and doc.folder_path == item.folder_path
        and doc.modified_date is not None
        and doc.modified_date == item.modified_date
        and (not item.size or not doc.file_size or doc.file_size == item.size)
    )
    if not same_file:
        return False
    if doc.status == INDEXED:
        return doc.index_version == current
    if doc.status == UNSUPPORTED:
        return doc.index_version == current_unsupported
    return False  # FAILED or PENDING: try again


@trace_worker("sync")
async def run_sync(job: IngestionJob) -> None:
    """Re-checks every document (re-index changed files, purge deleted ones) and indexes files that
    were added to tracked folders. Nothing is deleted when a drive cannot be reached."""
    progress = _Progress(job)
    current, current_unsupported = index_version(), unsupported_version()
    async with AsyncSessionLocal() as session:
        docs = (await session.execute(select(Document).where(Document.user_id == job.user_id))).scalars().all()
        folders = (await session.execute(
            select(TrackedFolder).where(TrackedFolder.user_id == job.user_id)
        )).scalars().all()
    active = [d for d in docs if d.status not in (DELETED, REMOVED)]
    removed: Set[Tuple[str, str]] = {(d.drive_type, d.onedrive_file_id) for d in docs if d.status == REMOVED}
    progress.total = len(active)
    await progress.save()

    for drive_type in sorted({d.drive_type for d in active} | {f.drive_type for f in folders}):
        drive_docs = [d for d in active if d.drive_type == drive_type]
        try:
            connector, tokens, token = await _connect(job.user_id, drive_type)
        except ConnectorError as exc:
            for doc in drive_docs:
                progress.add(doc.filename, "FAILED", reason=str(exc))
            continue

        # 1. List tracked folders: finds new files and saves one metadata call per known file.
        listed: Dict[str, DriveItem] = {}
        for folder in (f for f in folders if f.drive_type == drive_type):
            try:
                item = await connector.get_file(token, folder.item_id)
                if not item.is_folder:
                    raise NotFoundError("Not a folder")
                for f in await walk_files(connector, token, item.id, item.path, settings.MAX_TRACKED_FILES):
                    listed.setdefault(f.id, f)
            except NotFoundError:
                logger.info("Tracked folder %s is gone; no longer syncing it", folder.item_id)
                await _untrack(folder.id)
                continue
            except Exception as exc:  # incomplete listing: known files are still checked one by one
                logger.warning("Could not list tracked folder %s: %s", folder.item_id, exc)
                continue
            await track_folder(job.user_id, item)

        # 2. Known documents: unchanged, changed, or deleted.
        to_index: List[DriveItem] = []
        for doc in drive_docs:
            item = listed.get(doc.onedrive_file_id)
            if item is None:
                try:
                    item = await connector.get_file(await tokens.get(), doc.onedrive_file_id)
                except NotFoundError:
                    await mark_deleted(doc.id)
                    progress.add(doc.filename, DELETED)
                    continue
                except ConnectorError as exc:
                    progress.add(doc.filename, "FAILED", reason=str(exc))
                    continue
                except Exception:
                    logger.exception("Unexpected error checking %s", doc.filename)
                    progress.add(doc.filename, "FAILED", reason="Unexpected error")
                    continue
            if _up_to_date(doc, item, current, current_unsupported):
                progress.add(doc.filename, "UNCHANGED")
            else:
                to_index.append(item)

        # 3. New files in tracked folders (files the user removed from the knowledge base stay out).
        known = {d.onedrive_file_id for d in drive_docs}
        new_files = [f for f in listed.values() if f.id not in known and (drive_type, f.id) not in removed]
        if len(new_files) > settings.MAX_FILES_PER_JOB:
            logger.info("Sync found %d new files; indexing %d now, the rest on the next sync",
                        len(new_files), settings.MAX_FILES_PER_JOB)
            new_files = new_files[: settings.MAX_FILES_PER_JOB]
        progress.total += len(new_files)
        await progress.save()
        await _process_files(job.user_id, connector, tokens, to_index + new_files, progress)
    await _finish(progress)


async def _finish(progress: _Progress) -> None:
    status = COMPLETED if progress.failed == 0 else (FAILED if progress.processed == 0 else COMPLETED_WITH_ERRORS)
    error = None
    if progress.failed:
        reasons = sorted({f.get("reason") for f in progress.files if f["status"] == "FAILED" and f.get("reason")})
        error = "; ".join(reasons)[:1000] or None
    await progress.save(status=status, finished_at=utcnow(), error_message=error)


async def _heartbeat(job_id: str, attempt: int) -> None:
    """Keeps a long job (one huge file, slow OCR, a big sync) from looking abandoned to other workers."""
    interval = max(1.0, min(60.0, settings.WORKER_STALE_JOB_SECONDS / 4))
    while True:
        await asyncio.sleep(interval)
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(update(IngestionJob).where(
                    *_owned(job_id, attempt), IngestionJob.status == PROCESSING
                ).values(heartbeat_at=utcnow()))
                await session.commit()
        except Exception:
            logger.warning("Job heartbeat failed", exc_info=True)


async def _release(job_id: str, attempt: int) -> None:
    """Hands an interrupted job back without counting the interruption as a failed attempt."""
    async with AsyncSessionLocal() as session:
        await session.execute(update(IngestionJob).where(
            *_owned(job_id, attempt), IngestionJob.status == PROCESSING
        ).values(status=PENDING, heartbeat_at=None, attempts=func.greatest(IngestionJob.attempts - 1, 0)))
        await session.commit()


async def run_job(job: IngestionJob) -> None:
    beat = asyncio.create_task(_heartbeat(job.id, job.attempts), name=f"job-heartbeat-{job.id}")
    try:
        if job.job_type == SYNC:
            await run_sync(job)
        else:
            await run_ingest(job)
    except asyncio.CancelledError:
        # The worker is shutting down: hand the job back so another worker resumes it right away.
        await asyncio.shield(_release(job.id, job.attempts))
        raise
    except JobLostError:
        logger.warning("Job %s was taken over by another worker; stopping this run", job.id)
    except Exception as exc:
        logger.exception("Job %s failed", job.id)
        async with AsyncSessionLocal() as session:
            await session.execute(update(IngestionJob).where(*_owned(job.id, job.attempts)).values(
                status=FAILED, finished_at=utcnow(), error_message=f"Internal error: {type(exc).__name__}",
            ))
            await session.commit()
    finally:
        beat.cancel()


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


__all__ = ["INGEST", "SYNC", "TooManyJobsError", "enqueue", "drain_jobs", "track_folder", "worker"]
