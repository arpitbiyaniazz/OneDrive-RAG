"""Tracked folders, verified emails, one active sync per user, chat rate-limit index.

- tracked_folders: folders the user chose to index; sync lists them again to pick up new files.
  Back-filled from the folder selections of earlier ingestion jobs (their paths are refreshed
  on the next sync).
- users.email_verified: accounts are linked by email only when both providers verified it.
  Accounts created through Google are marked verified; Microsoft's `mail` is never verified.
- ingestion_jobs: at most one queued/running sync per user (duplicates are closed first).
- chat_messages: (user_id, created_at) index for per-user rate limiting.

Revision ID: 0003_tracked_folders
Revises: 0002_production_schema
Create Date: 2026-10-18
"""
from alembic import op

revision = "0003_tracked_folders"
down_revision = "0002_production_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tracked_folders (
            id varchar(36) PRIMARY KEY,
            user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            drive_type varchar(50) NOT NULL,
            item_id varchar(255) NOT NULL,
            name varchar(1000) NOT NULL DEFAULT '',
            path varchar(2000) NOT NULL DEFAULT '/',
            created_at timestamptz,
            last_synced_at timestamptz
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_tracked_folders_user_id ON tracked_folders (user_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tracked_user_drive_item ON tracked_folders (user_id, drive_type, item_id)"
    )
    op.execute(
        """
        INSERT INTO tracked_folders (id, user_id, drive_type, item_id, name, path, created_at)
        SELECT gen_random_uuid()::text, j.user_id, item->>'drive_type', item->>'id',
               left(coalesce(max(item->>'name'), ''), 1000), '/', min(j.created_at)
        FROM ingestion_jobs j, json_array_elements(
            CASE WHEN json_typeof(j.payload->'items') = 'array' THEN j.payload->'items' ELSE '[]'::json END
        ) AS item
        WHERE j.job_type = 'ingest'
          AND item->>'is_folder' = 'true'
          AND item->>'drive_type' IN ('onedrive', 'google_drive')
          AND coalesce(item->>'id', '') <> ''
        GROUP BY j.user_id, item->>'drive_type', item->>'id'
        ON CONFLICT DO NOTHING
        """
    )

    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified boolean NOT NULL DEFAULT false")
    op.execute("UPDATE users SET email_verified = true WHERE google_id IS NOT NULL AND microsoft_id IS NULL")

    op.execute(
        """
        UPDATE ingestion_jobs j SET status = 'FAILED', error_message = 'Superseded by a newer sync'
        WHERE j.job_type = 'sync' AND j.status IN ('PENDING', 'PROCESSING')
          AND EXISTS (SELECT 1 FROM ingestion_jobs n
                      WHERE n.user_id = j.user_id AND n.job_type = 'sync' AND n.status IN ('PENDING', 'PROCESSING')
                        AND (n.created_at, n.id) > (j.created_at, j.id))
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_sync_per_user ON ingestion_jobs (user_id) "
        "WHERE job_type = 'sync' AND status IN ('PENDING', 'PROCESSING')"
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_messages_user_created ON chat_messages (user_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chat_messages_user_created")
    op.execute("DROP INDEX IF EXISTS uq_active_sync_per_user")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email_verified")
    op.execute("DROP TABLE IF EXISTS tracked_folders")
