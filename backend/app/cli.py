"""Operational commands.

    python -m app.cli reset-embeddings     # after changing EMBEDDING_MODEL / EMBEDDING_DIMENSION
    python -m app.cli sync --email USER    # queue a full re-check/re-index for one user (or --all)
    python -m app.cli run-jobs             # process queued jobs in the foreground, then exit
"""
import argparse
import asyncio

from sqlalchemy import select, text

from app.core.config import settings
from app.db.database import AsyncSessionLocal, engine
from app.models.ingestion_job import SYNC
from app.models.user import User
from app.services import jobs


async def reset_embeddings() -> None:
    dim = int(settings.EMBEDDING_DIMENSION)
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS idx_chunk_embedding_hnsw"))
        await conn.execute(text("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding"))
        await conn.execute(text(f"ALTER TABLE document_chunks ADD COLUMN embedding vector({dim})"))
        await conn.execute(text("UPDATE document_chunks SET embedding_model = NULL"))
        await conn.execute(text(
            "CREATE INDEX idx_chunk_embedding_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops)"
        ))
        await conn.execute(text("UPDATE documents SET index_version = NULL"))
    print(f"Embedding column reset to vector({dim}). Run `python -m app.cli sync --all` to re-index.")


async def queue_sync(email: str | None, all_users: bool) -> None:
    async with AsyncSessionLocal() as session:
        stmt = select(User.id, User.email) if all_users else select(User.id, User.email).where(User.email == email)
        users = (await session.execute(stmt)).all()
    if not users:
        print("No matching users.")
    for user in users:
        job = await jobs.enqueue(user.id, SYNC, {})
        print(f"Queued sync {job.id} for {user.email}")


async def run_jobs() -> None:
    count = await jobs.drain_jobs(max_jobs=1000)
    print(f"Processed {count} job(s).")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reset-embeddings")
    sync = sub.add_parser("sync")
    sync.add_argument("--email")
    sync.add_argument("--all", action="store_true")
    sub.add_parser("run-jobs")
    args = parser.parse_args()
    if args.command == "reset-embeddings":
        asyncio.run(reset_embeddings())
    elif args.command == "sync":
        if not args.email and not args.all:
            parser.error("sync needs --email or --all")
        asyncio.run(queue_sync(args.email, args.all))
    else:
        asyncio.run(run_jobs())


if __name__ == "__main__":
    main()
