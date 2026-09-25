import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.database import engine
from app.db.init_db import init_db
from app.core.telemetry.otel_setup import init_opentelemetry
from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.api.onedrive import router as onedrive_router
from app.api.gdrive import router as gdrive_router
from app.api.ingestion import router as ingestion_router
from app.api.chat import router as chat_router
from app.api.sync import router as sync_router
from app.api.webhooks import router as webhooks_router

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("onedrive_rag")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting up {settings.PROJECT_NAME} v{settings.VERSION}...")
    try:
        await init_db()
    except Exception as e:
        logger.warning(f"Database initialization warning (will retry on first connection): {e}")
    yield
    logger.info("Shutting down application...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Production-Ready OneDrive Metadata-Aware RAG with Dual-Branch Observability (OpenTelemetry + Langfuse)",
    lifespan=lifespan,
)

# OpenTelemetry Instrumentation
init_opentelemetry(app=app, engine=engine)

# CORS Configuration
origins = [
    settings.FRONTEND_URL,
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Routers
app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(onedrive_router, prefix="/api")
app.include_router(gdrive_router, prefix="/api")
app.include_router(ingestion_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(sync_router, prefix="/api")
app.include_router(webhooks_router, prefix="/api")


@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME}",
        "version": settings.VERSION,
        "docs": "/docs",
        "health": "/api/health",
        "telemetry": "/api/telemetry/status",
    }
