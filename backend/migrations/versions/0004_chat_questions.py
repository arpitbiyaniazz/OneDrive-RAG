"""Rate limits count questions in their own table, so deleting chats no longer resets them.

- chat_questions: when each question was asked (no text). Back-filled from the last day of user
  messages so nobody's limits reset on upgrade.
- chat_messages: the (user_id, created_at) index was only used by the rate limits.

Revision ID: 0004_chat_questions
Revises: 0003_tracked_folders
Create Date: 2026-10-21
"""
from alembic import op

revision = "0004_chat_questions"
down_revision = "0003_tracked_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_questions (
            id varchar(36) PRIMARY KEY,
            user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_questions_user_created ON chat_questions (user_id, created_at)")
    op.execute(
        """
        INSERT INTO chat_questions (id, user_id, created_at)
        SELECT gen_random_uuid()::text, user_id, created_at FROM chat_messages
        WHERE role = 'user' AND created_at > now() - interval '1 day'
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_chat_messages_user_created")


def downgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_user_created ON chat_messages (user_id, created_at)")
    op.execute("DROP TABLE IF EXISTS chat_questions")
