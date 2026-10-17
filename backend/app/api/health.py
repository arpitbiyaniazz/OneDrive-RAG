from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.db.database import engine

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health():
    """Liveness: the process is up."""
    return {"status": "ok", "version": settings.VERSION, "sandbox_mode": settings.sandbox_mode}


@router.get("/health/ready")
async def ready():
    """Readiness: database reachable and migrated to the expected schema."""
    try:
        async with engine.connect() as conn:
            revision = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            await conn.execute(text("SELECT 1 FROM document_chunks LIMIT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "unreachable or not migrated"})
    return {"status": "ready", "database": "ok", "schema_revision": revision}
