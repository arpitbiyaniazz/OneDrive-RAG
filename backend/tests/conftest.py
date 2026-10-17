"""Test isolation: a dedicated database, sandbox drives and deterministic providers.

These variables are set before the app is imported so the developer's .env can never point
tests at a real database or real APIs. Live tests (RUN_LIVE_TESTS=1, `pytest -m live`) keep the
configured Mistral/OpenAI providers but still use the isolated test database.
"""
import os

LIVE = os.getenv("RUN_LIVE_TESTS") == "1"
os.environ.update({
    "ENVIRONMENT": "test",
    "DATABASE_URL": os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/onedrive_rag_test"),
    "DEV_MOCK_ONEDRIVE": "true",
    "DEV_MOCK_GDRIVE": "true",
    "LANGFUSE_ENABLED": "false",
    "OTEL_ENABLED": "false",
    "RUN_WORKER": "false",
})
if not LIVE:
    os.environ.update({
        "LLM_PROVIDER": "mock",
        "EMBEDDING_PROVIDER": "mock",
        "EMBEDDING_DIMENSION": "1024",
        "MISTRAL_API_KEY": "",
        "OPENAI_API_KEY": "",
    })

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.security import create_access_token  # noqa: E402
from app.db.database import AsyncSessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.connectors.sandbox import sandbox_gdrive, sandbox_onedrive  # noqa: E402

_TABLES = "chat_messages, chat_sessions, document_chunks, documents, ingestion_jobs, oauth_accounts, users"


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls real LLM/embedding APIs (set RUN_LIVE_TESTS=1)")


def pytest_collection_modifyitems(config, items):
    if LIVE:
        return
    skip = pytest.mark.skip(reason="live test: set RUN_LIVE_TESTS=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture(autouse=True)
async def clean_state():
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {_TABLES} CASCADE"))
    sandbox_onedrive.reset()
    sandbox_gdrive.reset()
    yield


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_headers(client):
    res = await client.post("/api/auth/sandbox")
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


async def make_user(email: str) -> tuple[User, dict]:
    async with AsyncSessionLocal() as session:
        user = User(email=email, full_name=email.split("@")[0])
        session.add(user)
        await session.commit()
        return user, {"Authorization": f"Bearer {create_access_token(user.id, 0)}"}


async def user_id_for(headers: dict, client) -> str:
    return (await client.get("/api/auth/me", headers=headers)).json()["id"]
