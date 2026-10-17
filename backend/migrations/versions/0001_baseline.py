"""Baseline: the schema created by the original create_all-based startup.

Databases that already have it (created before Alembic was introduced) are left untouched,
so `alembic upgrade head` works on both fresh and legacy databases.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

LEGACY_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
    id varchar(36) PRIMARY KEY,
    email varchar(255) NOT NULL,
    full_name varchar(255),
    microsoft_id varchar(255),
    google_id varchar(255),
    is_active boolean,
    created_at timestamptz,
    updated_at timestamptz
);
CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE UNIQUE INDEX ix_users_microsoft_id ON users (microsoft_id);

CREATE TABLE oauth_accounts (
    id varchar(36) PRIMARY KEY,
    user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider varchar(50) NOT NULL,
    encrypted_access_token text NOT NULL,
    encrypted_refresh_token text,
    expires_at timestamptz,
    token_type varchar(50),
    scope text,
    created_at timestamptz,
    updated_at timestamptz
);
CREATE INDEX ix_oauth_accounts_user_id ON oauth_accounts (user_id);

CREATE TABLE documents (
    id varchar(36) PRIMARY KEY,
    user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    onedrive_file_id varchar(255) NOT NULL,
    filename varchar(255) NOT NULL,
    file_type varchar(50) NOT NULL,
    folder_id varchar(255),
    folder_path varchar(1000) NOT NULL,
    mime_type varchar(150),
    file_size bigint,
    created_date timestamptz,
    modified_date timestamptz,
    onedrive_url text,
    content_hash varchar(64),
    last_ingested_at timestamptz,
    status varchar(50) NOT NULL,
    created_at timestamptz,
    updated_at timestamptz,
    drive_type varchar(50) DEFAULT 'onedrive'
);
CREATE UNIQUE INDEX idx_doc_user_onedrive ON documents (user_id, onedrive_file_id);
CREATE INDEX idx_doc_user_folder ON documents (user_id, folder_path);
CREATE INDEX idx_doc_user_modified ON documents (user_id, modified_date);
CREATE INDEX ix_documents_user_id ON documents (user_id);
CREATE INDEX ix_documents_onedrive_file_id ON documents (onedrive_file_id);
CREATE INDEX ix_documents_filename ON documents (filename);
CREATE INDEX ix_documents_file_type ON documents (file_type);
CREATE INDEX ix_documents_folder_path ON documents (folder_path);
CREATE INDEX ix_documents_modified_date ON documents (modified_date);
CREATE INDEX ix_documents_content_hash ON documents (content_hash);
CREATE INDEX ix_documents_status ON documents (status);

CREATE TABLE document_chunks (
    id varchar(36) PRIMARY KEY,
    document_id varchar(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    embedding vector(1536),
    page_number integer,
    section varchar(255),
    metadata_json json,
    created_at timestamptz
);
CREATE INDEX idx_chunk_user_doc ON document_chunks (user_id, document_id);
CREATE INDEX ix_document_chunks_document_id ON document_chunks (document_id);
CREATE INDEX ix_document_chunks_user_id ON document_chunks (user_id);
CREATE INDEX ix_document_chunks_page_number ON document_chunks (page_number);

CREATE TABLE ingestion_jobs (
    id varchar(36) PRIMARY KEY,
    user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status varchar(50) NOT NULL,
    folder_path varchar(1000),
    total_files integer,
    processed_files integer,
    failed_files integer,
    total_chunks integer,
    error_message text,
    created_at timestamptz,
    updated_at timestamptz
);
CREATE INDEX ix_ingestion_jobs_user_id ON ingestion_jobs (user_id);
CREATE INDEX ix_ingestion_jobs_status ON ingestion_jobs (status);

CREATE TABLE chat_sessions (
    id varchar(36) PRIMARY KEY,
    user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title varchar(255) NOT NULL,
    created_at timestamptz,
    updated_at timestamptz
);
CREATE INDEX ix_chat_sessions_user_id ON chat_sessions (user_id);

CREATE TABLE chat_messages (
    id varchar(36) PRIMARY KEY,
    session_id varchar(36) NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role varchar(50) NOT NULL,
    content text NOT NULL,
    citations json,
    langfuse_trace_id varchar(100),
    feedback integer,
    created_at timestamptz
);
CREATE INDEX ix_chat_messages_session_id ON chat_messages (session_id);
CREATE INDEX ix_chat_messages_user_id ON chat_messages (user_id);
CREATE INDEX ix_chat_messages_langfuse_trace_id ON chat_messages (langfuse_trace_id);
"""


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("users"):
        return  # legacy database created by create_all
    for statement in LEGACY_SCHEMA.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in ("chat_messages", "chat_sessions", "ingestion_jobs", "document_chunks", "documents", "oauth_accounts", "users"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
