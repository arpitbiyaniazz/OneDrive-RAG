"""Langfuse LLM tracing (optional). Every helper is a no-op when Langfuse is not configured."""
import contextlib
import logging
from functools import lru_cache
from typing import Any, Iterator, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_langfuse() -> Optional[Any]:
    if not (settings.LANGFUSE_ENABLED and settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
        return None
    if settings.ENVIRONMENT == "test":
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
            environment=settings.ENVIRONMENT,
            release=settings.VERSION,
        )
    except Exception:
        logger.exception("Langfuse initialization failed; LLM tracing disabled")
        return None


class _Trace:
    def __init__(self, span: Any = None, trace_id: Optional[str] = None):
        self._span = span
        self.trace_id = trace_id

    def update(self, **kwargs: Any) -> None:
        if self._span is not None:
            try:
                self._span.update(**kwargs)
            except Exception:
                logger.debug("Langfuse span update failed", exc_info=True)


@contextlib.contextmanager
def rag_trace(name: str, **kwargs: Any) -> Iterator[_Trace]:
    """Opens a Langfuse observation; LLM calls made inside it (via the OpenAI wrapper) nest under it."""
    client = get_langfuse()
    if client is None:
        yield _Trace()
        return
    try:
        manager = client.start_as_current_observation(name=name, as_type="chain", **kwargs)
        span = manager.__enter__()
    except Exception:
        logger.debug("Langfuse trace start failed", exc_info=True)
        yield _Trace()
        return
    try:
        yield _Trace(span, getattr(span, "trace_id", None))
    finally:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            # The streaming generator may finish in another context after a client disconnect.
            logger.debug("Langfuse trace close failed", exc_info=True)


def log_score(trace_id: Optional[str], name: str, value: float, comment: Optional[str] = None,
              score_id: Optional[str] = None) -> bool:
    """Records a score; the same `score_id` updates the earlier score instead of adding one."""
    client = get_langfuse()
    if client is None or not trace_id:
        return False
    try:
        client.create_score(trace_id=trace_id, name=name, value=value, comment=comment, score_id=score_id)
        return True
    except Exception:
        logger.warning("Failed to record Langfuse score", exc_info=True)
        return False


def shutdown_langfuse() -> None:
    client = get_langfuse()
    if client is not None:
        try:
            client.shutdown()
        except Exception:
            logger.debug("Langfuse shutdown failed", exc_info=True)
