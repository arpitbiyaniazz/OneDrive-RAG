from functools import cached_property
from typing import List, Literal, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Development-only fallbacks. Production refuses to start with these (see _check_production).
_DEV_SECRET_KEY = "dev-only-secret-key-not-for-production-use-0000000000"
_DEV_TOKEN_ENCRYPTION_KEY = "dev-only-token-encryption-key-not-for-production-0000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    PROJECT_NAME: str = "Cloud Drive RAG Assistant"
    VERSION: str = "2.0.0"
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["text", "json"] = "text"

    # Microsoft Graph / OAuth 2.0
    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT_ID: str = "common"
    MICROSOFT_REDIRECT_URI: str = "http://localhost:5173/auth/callback"
    ONEDRIVE_ROOT_FOLDER: str = "OneDrive-RAG"

    # Google Drive / OAuth 2.0
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:5173/auth/google/callback"
    GOOGLE_DRIVE_ROOT_FOLDER: str = "OneDrive-RAG"

    # Sandbox connectors with generated sample files. Local development and tests only.
    DEV_MOCK_ONEDRIVE: bool = False
    DEV_MOCK_GDRIVE: bool = False

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/onedrive_rag"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 10

    # Chat model
    LLM_PROVIDER: Literal["mistral", "openai", "mock"] = "mistral"
    MISTRAL_API_KEY: str = ""
    MISTRAL_MODEL: str = "mistral-small-latest"
    MISTRAL_API_BASE: str = "https://api.mistral.ai/v1"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_API_BASE: Optional[str] = None
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 1200
    LLM_TIMEOUT_SECONDS: float = 60.0
    QUERY_REWRITE_TIMEOUT_SECONDS: float = 10.0

    # Embeddings. EMBEDDING_DIMENSION must match the model (mistral-embed: 1024,
    # text-embedding-3-small: 1536). Changing it requires `alembic upgrade head` + a re-sync.
    EMBEDDING_PROVIDER: Literal["mistral", "openai", "mock"] = "mistral"
    EMBEDDING_MODEL: str = "mistral-embed"
    EMBEDDING_DIMENSION: int = 1024
    EMBEDDING_BATCH_SIZE: int = 16

    # Retrieval and context assembly
    RETRIEVAL_TOP_K: int = 8
    RETRIEVAL_CANDIDATES: int = 40
    RETRIEVAL_MAX_CHUNKS_PER_DOCUMENT: int = 4
    CONTEXT_MAX_CHARS: int = 24000
    CHAT_HISTORY_TURNS: int = 6

    # Limits
    MAX_QUERY_CHARS: int = 4000
    MAX_FILE_SIZE_MB: int = 50
    MAX_FILES_PER_JOB: int = 500
    MAX_CHUNKS_PER_DOCUMENT: int = 5000

    # Background worker (runs inside the API process; safe with several replicas)
    RUN_WORKER: bool = True
    WORKER_POLL_SECONDS: float = 2.0
    WORKER_STALE_JOB_SECONDS: int = 600

    # OpenTelemetry
    OTEL_ENABLED: bool = True
    OTEL_SERVICE_NAME: str = "cloud-drive-rag-backend"
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
    SECRET_KEY: str = _DEV_SECRET_KEY
    TOKEN_ENCRYPTION_KEY: str = _DEV_TOKEN_ENCRYPTION_KEY
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12
    OAUTH_STATE_TTL_SECONDS: int = 600

    # CORS: comma-separated list of allowed browser origins
    FRONTEND_URL: str = "http://localhost:5173"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    @cached_property
    def cors_origins(self) -> List[str]:
        origins = {o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()}
        origins.add(self.FRONTEND_URL)
        return sorted(origins)

    @property
    def sandbox_mode(self) -> bool:
        return self.DEV_MOCK_ONEDRIVE or self.DEV_MOCK_GDRIVE

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024

    @model_validator(mode="after")
    def _check_production(self) -> "Settings":
        if self.ENVIRONMENT != "production":
            return self
        problems = []
        if self.SECRET_KEY == _DEV_SECRET_KEY or len(self.SECRET_KEY) < 32:
            problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
        if self.TOKEN_ENCRYPTION_KEY == _DEV_TOKEN_ENCRYPTION_KEY or len(self.TOKEN_ENCRYPTION_KEY) < 32:
            problems.append("TOKEN_ENCRYPTION_KEY must be set to a random value of at least 32 characters")
        if self.sandbox_mode:
            problems.append("DEV_MOCK_ONEDRIVE and DEV_MOCK_GDRIVE must be false in production")
        if self.LLM_PROVIDER == "mock" or self.EMBEDDING_PROVIDER == "mock":
            problems.append("LLM_PROVIDER and EMBEDDING_PROVIDER cannot be 'mock' in production")
        for provider in {self.LLM_PROVIDER, self.EMBEDDING_PROVIDER}:
            if provider == "mistral" and not self.MISTRAL_API_KEY:
                problems.append("MISTRAL_API_KEY is required for the configured provider")
            if provider == "openai" and not self.OPENAI_API_KEY:
                problems.append("OPENAI_API_KEY is required for the configured provider")
        if problems:
            raise ValueError("Invalid production configuration: " + "; ".join(sorted(set(problems))))
        return self


settings = Settings()
