import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.drives import router as drives_router
from app.api.health import router as health_router
from app.api.ingestion import router as ingestion_router
from app.core.config import settings
from app.core.telemetry.langfuse_client import shutdown_langfuse
from app.core.telemetry.otel_setup import init_opentelemetry
from app.db.database import engine
from app.services.connectors.base import close_http_client
from app.services.diagnostics import log_provider_problems
from app.services.jobs import worker

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        _JsonFormatter() if settings.LOG_FORMAT == "json"
        else logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.LOG_LEVEL.upper())
    for noisy in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger("app")


async def _check_schema() -> None:
    """Fails fast when migrations are missing or the vector column does not match EMBEDDING_DIMENSION."""
    async with engine.connect() as conn:
        dim = (await conn.execute(text(
            "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
        ))).scalar()
    if dim is not None and dim != settings.EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"document_chunks.embedding is vector({dim}) but EMBEDDING_DIMENSION={settings.EMBEDDING_DIMENSION}. "
            "Run `python -m app.cli reset-embeddings` and then a sync to re-index."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s (%s)", settings.PROJECT_NAME, settings.VERSION, settings.ENVIRONMENT)
    provider_check = None
    if settings.ENVIRONMENT != "test":
        await _check_schema()
        provider_check = asyncio.create_task(log_provider_problems(), name="provider-check")
        if settings.RUN_WORKER:
            worker.start()
    yield
    if provider_check is not None and not provider_check.done():
        provider_check.cancel()
    await worker.stop()
    await close_http_client()
    shutdown_langfuse()
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url=None,
)
init_opentelemetry(app=app, engine=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    # WWW-Authenticate lets a cross-origin SPA tell an expired session (401) from other errors.
    expose_headers=["X-Chat-Session-Id", "X-Request-Id", "WWW-Authenticate"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id", "")[:64] or uuid.uuid4().hex[:16]
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path != "/api/health":
            logger.info("%s %s -> %s (%.0f ms)", request.method, request.url.path, response.status_code,
                        (time.perf_counter() - start) * 1000)
        return response
    finally:
        request_id_var.reset(token)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


for router in (health_router, auth_router, drives_router, ingestion_router, chat_router):
    app.include_router(router, prefix="/api")
