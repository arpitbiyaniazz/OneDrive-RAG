import asyncio
import logging
from sqlalchemy import text
from app.db.database import engine, Base
import app.models  # Ensure all models are registered

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def init_db():
    logger.info("Initializing database and pgvector extension...")
    async with engine.begin() as conn:
        try:
            # Enable pgvector extension
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            logger.info("pgvector extension verified.")
        except Exception as e:
            logger.warning(f"Note on vector extension: {e}")

        # Create all tables
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables verified/created successfully.")


if __name__ == "__main__":
    asyncio.run(init_db())
