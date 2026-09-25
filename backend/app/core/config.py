from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # App Info
    PROJECT_NAME: str = "OneDrive Metadata-Aware RAG Chatbot"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Microsoft Graph / OAuth 2.0
    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT_ID: str = "common"
    MICROSOFT_REDIRECT_URI: str = "http://localhost:5173/auth/callback"
    DEV_MOCK_ONEDRIVE: bool = True
    WEBHOOK_CLIENT_STATE: str = "onedrive-rag-webhook-secret-token"

    # Google Drive / OAuth 2.0
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:5173/auth/google/callback"
    DEV_MOCK_GDRIVE: bool = True
    GOOGLE_WEBHOOK_SECRET: str = "gdrive-rag-webhook-secret-token"


    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/onedrive_rag"
    DATABASE_URL_SYNC: str = "postgresql://postgres:postgres@localhost:5432/onedrive_rag"

    # AI Models
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSION: int = 1536

    # OpenTelemetry
    OTEL_SERVICE_NAME: str = "onedrive-rag-backend"
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None
    OTEL_EXPORTER_OTLP_HEADERS: Optional[str] = None
    OTEL_EXPORTER_OTLP_INSECURE: Optional[bool] = None
    OTEL_CONSOLE_EXPORTER: bool = False

    # Langfuse
    LANGFUSE_PUBLIC_KEY: Optional[str] = None
    LANGFUSE_SECRET_KEY: Optional[str] = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    LANGFUSE_ENABLED: bool = True

    # Security
    SECRET_KEY: str = "dev-secret-key-change-in-production-min-32-chars-long!"
    TOKEN_ENCRYPTION_KEY: str = "dev-token-encryption-key-32-bytes!"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # CORS
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"


settings = Settings()
