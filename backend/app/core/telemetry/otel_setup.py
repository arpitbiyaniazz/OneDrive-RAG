import contextlib
import functools
import logging
from typing import Any, Callable, Dict, Iterator, Optional
from urllib.parse import urlsplit, urlunsplit

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode

from app.core.config import settings

logger = logging.getLogger(__name__)


# Hosts whose URL paths are safe to keep in traces; for any other host (for example OneDrive's
# pre-authenticated download redirects, which carry a bearer token in the URL) only the host is kept.
_API_HOSTS = ("graph.microsoft.com", "www.googleapis.com", "oauth2.googleapis.com", "login.microsoftonline.com",
              "openidconnect.googleapis.com", "api.mistral.ai", "api.openai.com")
_URL_ATTRIBUTES = ("url.full", "http.url")


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.hostname in _API_HOSTS:
        return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
    return urlunsplit((parts.scheme, (parts.hostname or ""), "/", "", ""))


class _RedactingProcessor(SpanProcessor):
    """Rewrites URL attributes as spans start, before any exporter can see them."""

    def on_start(self, span, parent_context=None) -> None:
        attributes = getattr(span, "attributes", None) or {}
        for key in _URL_ATTRIBUTES:
            value = attributes.get(key)
            if isinstance(value, str):
                span.set_attribute(key, redact_url(value))


def init_opentelemetry(app=None, engine=None) -> Optional[TracerProvider]:
    """Configures tracing and auto-instrumentation for FastAPI, SQLAlchemy and HTTPX."""
    if not settings.OTEL_ENABLED or settings.ENVIRONMENT == "test":
        return None
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        provider = TracerProvider(resource=Resource.create({
            "service.name": settings.OTEL_SERVICE_NAME,
            "service.version": settings.VERSION,
            "deployment.environment": settings.ENVIRONMENT,
        }))
        provider.add_span_processor(_RedactingProcessor())  # must run before the exporting processors
        if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            insecure = settings.OTEL_EXPORTER_OTLP_INSECURE
            if insecure is None:
                insecure = not settings.OTEL_EXPORTER_OTLP_ENDPOINT.startswith("https://")
            kwargs: Dict[str, Any] = {"endpoint": settings.OTEL_EXPORTER_OTLP_ENDPOINT, "insecure": insecure}
            if settings.OTEL_EXPORTER_OTLP_HEADERS:
                kwargs["headers"] = tuple(
                    tuple(part.strip() for part in item.split("=", 1))
                    for item in settings.OTEL_EXPORTER_OTLP_HEADERS.split(",") if "=" in item
                )
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(**kwargs)))
            logger.info("OpenTelemetry OTLP exporter -> %s", settings.OTEL_EXPORTER_OTLP_ENDPOINT)
        elif settings.OTEL_CONSOLE_EXPORTER:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        if app is not None:
            FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="api/health")
        if engine is not None:
            SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=provider)
        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
        return provider
    except Exception:
        logger.exception("OpenTelemetry initialization failed; continuing without tracing")
        return None


def get_tracer(name: str = "cloud-drive-rag"):
    return trace.get_tracer(name)


def get_current_trace_id() -> Optional[str]:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


@contextlib.contextmanager
def _span(name: str, attributes: Dict[str, Any]) -> Iterator[Any]:
    with get_tracer().start_as_current_span(name, attributes=attributes) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def trace_drive_call(drive: str, operation: str):
    """Span around a OneDrive / Google Drive API operation."""
    return _span(f"{drive}.{operation}", {"drive.type": drive, "drive.operation": operation})


def trace_worker(job_name: str):
    """Decorator recording a span for an async background job."""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            with _span(f"worker.{job_name}", {"worker.job": job_name}):
                return await func(*args, **kwargs)
        return wrapper
    return decorator
