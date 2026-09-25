import functools
import logging
import os
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# OpenTelemetry imports with graceful fallbacks
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.trace import Status, StatusCode
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        OTLPSpanExporter = None

    OTEL_AVAILABLE = True
except ImportError as e:
    logger.warning(f"OpenTelemetry libraries not fully installed: {e}. Running with no-op tracer.")
    OTEL_AVAILABLE = False
    trace = None

from app.core.config import settings


def init_opentelemetry(app=None, engine=None):
    """
    Initializes OpenTelemetry TracerProvider, resource metadata,
    and auto-instrumentation for FastAPI, SQLAlchemy, and HTTPX.
    """
    if not OTEL_AVAILABLE:
        return None

    try:
        resource = Resource.create(attributes={
            "service.name": settings.OTEL_SERVICE_NAME,
            "service.version": settings.VERSION,
            "deployment.environment": settings.ENVIRONMENT,
        })

        provider = TracerProvider(resource=resource)

        # 1. Configure Exporters
        if settings.OTEL_EXPORTER_OTLP_ENDPOINT and OTLPSpanExporter:
            otlp_exporter = OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info(f"OpenTelemetry OTLP exporter enabled -> {settings.OTEL_EXPORTER_OTLP_ENDPOINT}")
        elif settings.OTEL_CONSOLE_EXPORTER:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            logger.info("OpenTelemetry Console exporter enabled.")

        trace.set_tracer_provider(provider)

        # 2. Instrument FastAPI
        if app is not None:
            FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
            logger.info("OpenTelemetry FastAPI auto-instrumentation active.")

        # 3. Instrument SQLAlchemy
        if engine is not None:
            SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=provider)
            logger.info("OpenTelemetry SQLAlchemy auto-instrumentation active.")

        # 4. Instrument HTTPX (captures Microsoft Graph & external API calls)
        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
        logger.info("OpenTelemetry HTTPX auto-instrumentation active.")

        return provider
    except Exception as exc:
        logger.error(f"Failed to initialize OpenTelemetry: {exc}")
        return None


def get_tracer(name: str = "onedrive-rag"):
    """Get standard OTel tracer."""
    if OTEL_AVAILABLE and trace:
        return trace.get_tracer(name)
    return None


def get_current_trace_id() -> Optional[str]:
    """Retrieve the current active OpenTelemetry trace ID as a 32-char hex string."""
    if not OTEL_AVAILABLE or not trace:
        return None
    span = trace.get_current_span()
    if span and span.get_span_context().is_valid:
        return format(span.get_span_context().trace_id, "032x")
    return None


def trace_worker(job_name: str, attributes: Optional[Dict[str, Any]] = None):
    """
    Decorator to record worker latency and execution spans
    in OpenTelemetry for background ingestion/parsing jobs.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            tracer = get_tracer("worker-tracer")
            if not tracer:
                return await func(*args, **kwargs)

            span_attrs = {"worker.job": job_name}
            if attributes:
                span_attrs.update(attributes)

            with tracer.start_as_current_span(f"worker.{job_name}", attributes=span_attrs) as span:
                start_time = time.perf_counter()
                try:
                    res = await func(*args, **kwargs)
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    span.set_attribute("worker.duration_ms", duration_ms)
                    span.set_status(Status(StatusCode.OK))
                    return res
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            tracer = get_tracer("worker-tracer")
            if not tracer:
                return func(*args, **kwargs)

            span_attrs = {"worker.job": job_name}
            if attributes:
                span_attrs.update(attributes)

            with tracer.start_as_current_span(f"worker.{job_name}", attributes=span_attrs) as span:
                start_time = time.perf_counter()
                try:
                    res = func(*args, **kwargs)
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    span.set_attribute("worker.duration_ms", duration_ms)
                    span.set_status(Status(StatusCode.OK))
                    return res
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise

        import inspect
        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper
    return decorator


def trace_onedrive_call(operation: str, resource_path: str = ""):
    """
    Context manager to record OneDrive API call latencies, resource IDs, and statuses.
    """
    class OneDriveSpanContext:
        def __enter__(self):
            tracer = get_tracer("onedrive-tracer")
            self.span = None
            if tracer:
                self.span = tracer.start_span(
                    f"onedrive.{operation}",
                    attributes={
                        "onedrive.operation": operation,
                        "onedrive.resource": resource_path,
                    }
                )
                self.start_time = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if self.span:
                duration_ms = (time.perf_counter() - self.start_time) * 1000
                self.span.set_attribute("onedrive.latency_ms", duration_ms)
                if exc_val:
                    self.span.record_exception(exc_val)
                    self.span.set_status(Status(StatusCode.ERROR, str(exc_val)))
                else:
                    self.span.set_status(Status(StatusCode.OK))
                self.span.end()

    return OneDriveSpanContext()


def trace_gdrive_call(operation: str, resource_path: str = ""):
    """
    Context manager to record Google Drive API call latencies, resource IDs, and statuses.
    """
    class GDriveSpanContext:
        def __enter__(self):
            tracer = get_tracer("gdrive-tracer")
            self.span = None
            if tracer:
                self.span = tracer.start_span(
                    f"gdrive.{operation}",
                    attributes={
                        "gdrive.operation": operation,
                        "gdrive.resource": resource_path,
                    }
                )
                self.start_time = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if self.span:
                duration_ms = (time.perf_counter() - self.start_time) * 1000
                self.span.set_attribute("gdrive.latency_ms", duration_ms)
                if exc_val:
                    self.span.record_exception(exc_val)
                    self.span.set_status(Status(StatusCode.ERROR, str(exc_val)))
                else:
                    self.span.set_status(Status(StatusCode.OK))
                self.span.end()

    return GDriveSpanContext()

