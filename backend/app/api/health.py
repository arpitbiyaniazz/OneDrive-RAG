from fastapi import APIRouter
from app.core.config import settings
from app.core.telemetry.langfuse_client import get_langfuse
from app.core.telemetry.otel_setup import get_tracer

router = APIRouter(prefix="", tags=["Health & Observability"])


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "mock_onedrive_mode": settings.DEV_MOCK_ONEDRIVE,
    }


@router.get("/telemetry/status")
async def telemetry_status():
    tracer = get_tracer()
    langfuse = get_langfuse()
    return {
        "opentelemetry": {
            "service_name": settings.OTEL_SERVICE_NAME,
            "tracer_active": tracer is not None,
            "otlp_endpoint": settings.OTEL_EXPORTER_OTLP_ENDPOINT or "None (local console)",
        },
        "langfuse": {
            "host": settings.LANGFUSE_HOST,
            "client_active": langfuse is not None,
            "configured": bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY),
        }
    }
