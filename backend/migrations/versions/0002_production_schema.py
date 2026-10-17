"""Production schema: hybrid search, embedding-model tracking, durable jobs, session revocation.

- document_chunks: embedding re-created with EMBEDDING_DIMENSION (existing vectors came from an
  unknown/mock model and are discarded), embedding_model, context header, page_end, section as
  text, generated full-text search column with a GIN index, HNSW cosine index.
- documents: provider-aware unique key, index_version, chunk_count, error_message, longer names.
  index_version is cleared so the next sync re-indexes every document with the new pipeline.
- ingestion_jobs: job_type, payload, result, attempts, heartbeat and timing columns.
- users: token_version (logout revokes sessions); unique google_id.
- oauth_accounts: one row per (user, provider).

Revision ID: 0002_production_schema
Revises: 0001_baseline
Create Date: 2026-10-17
"""
from alembic import op

from app.core.config import settings

revision = "0002_production_schema"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dim = int(settings.EMBEDDING_DIMENSION)

    # users
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id varchar(255)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 0")
    op.execute("UPDATE users SET is_active = true WHERE is_active IS NULL")
    op.execute("ALTER TABLE users ALTER COLUMN is_active SET NOT NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_id ON users (google_id)")

    # oauth_accounts: keep the newest row per (user, provider), then enforce uniqueness
    op.execute(
        """
        DELETE FROM oauth_accounts a USING oauth_accounts b
        WHERE a.user_id = b.user_id AND a.provider = b.provider
          AND (a.updated_at, a.id) < (b.updated_at, b.id)
        """
    )
    op.execute("ALTER TABLE oauth_accounts ADD CONSTRAINT uq_oauth_user_provider UNIQUE (user_id, provider)")

    # documents
    op.execute("ALTER TABLE documents ALTER COLUMN filename TYPE varchar(1000)")
    op.execute("ALTER TABLE documents ALTER COLUMN folder_path TYPE varchar(2000)")
    op.execute("ALTER TABLE documents ALTER COLUMN mime_type TYPE varchar(255)")
    op.execute("UPDATE documents SET drive_type = 'onedrive' WHERE drive_type IS NULL")
    op.execute("ALTER TABLE documents ALTER COLUMN drive_type SET NOT NULL")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS folder_id")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_version varchar(255)")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_count integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS error_message text")
    op.execute("DROP INDEX IF EXISTS idx_doc_user_onedrive")
    op.execute("DROP INDEX IF EXISTS ix_documents_content_hash")
    op.execute("DROP INDEX IF EXISTS ix_documents_folder_path")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_user_drive_file ON documents (user_id, drive_type, onedrive_file_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_drive_type ON documents (drive_type)")

    # document_chunks
    op.execute("ALTER TABLE document_chunks ALTER COLUMN section TYPE text")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS context text")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS page_end integer")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_model varchar(255)")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_page_number")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding")
    op.execute(f"ALTER TABLE document_chunks ADD COLUMN embedding vector({dim})")
    op.execute(
        """
        ALTER TABLE document_chunks ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', coalesce(context, '') || ' ' || content)) STORED
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunk_search_vector ON document_chunks USING gin (search_vector)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_embedding_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    # Old chunks have no usable vectors; force a full re-index on the next sync.
    op.execute("UPDATE documents SET index_version = NULL")

    # ingestion_jobs
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS job_type varchar(20) NOT NULL DEFAULT 'ingest'")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS payload json")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS result json")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS started_at timestamptz")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS finished_at timestamptz")
    # Jobs from the old in-process runner can never resume.
    op.execute(
        "UPDATE ingestion_jobs SET status = 'FAILED', error_message = 'Interrupted before upgrade' "
        "WHERE status IN ('PENDING', 'PROCESSING')"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_job_status_created ON ingestion_jobs (status, created_at)")


def downgrade() -> None:
    raise NotImplementedError("0002 discards legacy embeddings and cannot be reversed; restore from a backup.")
