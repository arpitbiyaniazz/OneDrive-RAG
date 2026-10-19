"""Configuration and provider checks shared by `python -m app.cli doctor` and the startup check.

Results never include secrets: only whether a value is set and what the provider answered.
"""
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import text

from app.core.config import settings
from app.db.database import engine

logger = logging.getLogger(__name__)
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _describe(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return f"the provider rejected the API key ({status}); check MISTRAL_API_KEY / OPENAI_API_KEY"
    if status:
        return f"provider error {status}: {getattr(exc, 'message', '') or type(exc).__name__}"
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def _head_revision() -> Optional[str]:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(os.path.join(_BACKEND_DIR, "alembic.ini"))
        config.set_main_option("script_location", os.path.join(_BACKEND_DIR, "migrations"))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        return None


async def check_database() -> List[Check]:
    try:
        async with engine.connect() as conn:
            revision = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            dim = (await conn.execute(text(
                "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
            ))).scalar()
    except Exception as exc:
        return [Check("database", False, f"cannot query the database ({type(exc).__name__}); run `alembic upgrade head`")]
    head = _head_revision()
    checks = [Check("database", True, "reachable")]
    checks.append(Check("migrations", head is None or revision == head,
                        f"at {revision}" + ("" if head is None or revision == head else f", expected {head}: run `alembic upgrade head`")))
    checks.append(Check("vector column", dim == settings.EMBEDDING_DIMENSION,
                        f"vector({dim}), EMBEDDING_DIMENSION={settings.EMBEDDING_DIMENSION}"
                        + ("" if dim == settings.EMBEDDING_DIMENSION else ": run `python -m app.cli reset-embeddings`")))
    return checks


async def check_llm() -> Check:
    from app.services.llm_service import get_llm_client

    label = f"chat model ({settings.LLM_PROVIDER})"
    try:
        client = get_llm_client()
        await asyncio.wait_for(client.check(), timeout=20)
        return Check(label, True, f"{client.model}: credentials accepted")
    except Exception as exc:
        return Check(label, False, _describe(exc))


async def check_embeddings() -> Check:
    from app.services.embedding_service import get_embedding_provider

    label = f"embeddings ({settings.EMBEDDING_PROVIDER})"
    try:
        provider = get_embedding_provider()
        vector = await asyncio.wait_for(provider.embed_query("connection check"), timeout=30)
        return Check(label, len(vector) == settings.EMBEDDING_DIMENSION, f"{provider.model_id}: {len(vector)} dimensions")
    except Exception as exc:
        return Check(label, False, _describe(exc))


def check_configuration() -> List[Check]:
    checks = []
    for name, configured, uri in (
        ("Microsoft sign-in", bool(settings.MICROSOFT_CLIENT_ID and settings.MICROSOFT_CLIENT_SECRET), settings.MICROSOFT_REDIRECT_URI),
        ("Google sign-in", bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET), settings.GOOGLE_REDIRECT_URI),
    ):
        checks.append(Check(name, configured, f"redirect URI {uri}" if configured else "not configured", required=False))
    checks.append(Check("OCR", True, "on (Mistral OCR)" if settings.OCR_PROVIDER != "none" else
                        "off: scanned PDFs and images are not indexed (OCR_PROVIDER=mistral turns it on)", required=False))
    if settings.sandbox_mode:
        checks.append(Check("sandbox drives", settings.ENVIRONMENT != "production", "on (development only)", required=False))
    if settings.ENVIRONMENT == "production" and "localhost" in settings.FRONTEND_URL:
        checks.append(Check("FRONTEND_URL", False, f"{settings.FRONTEND_URL} looks like a development URL", required=False))
    return checks


async def run_all() -> List[Check]:
    checks = await check_database()
    checks += [await check_llm(), await check_embeddings()]
    return checks + check_configuration()


async def log_provider_problems() -> None:
    """Startup check: a rejected API key otherwise only shows up as failing answers and indexing."""
    for check in (await check_llm(), await check_embeddings()):
        if check.ok:
            logger.info("%s: %s", check.name, check.detail)
        else:
            logger.error("%s is not usable: %s. Chat answers and indexing will fail until this is fixed.",
                         check.name, check.detail)
