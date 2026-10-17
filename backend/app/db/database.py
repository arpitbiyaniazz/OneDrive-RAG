from datetime import UTC, datetime
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool

from app.core.config import settings

_engine_kwargs = {"pool_pre_ping": True}
if settings.ENVIRONMENT == "test":
    # Each pytest-asyncio test runs in its own event loop; pooled connections cannot cross loops.
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs.update(pool_size=settings.DB_POOL_SIZE, max_overflow=settings.DB_MAX_OVERFLOW)

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=AsyncSession)

Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(UTC)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding one session per request."""
    async with AsyncSessionLocal() as session:
        yield session
