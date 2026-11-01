"""A question can be limited to documents the user selected (by typing "/" in the chat input).

- chat_messages.documents: on a user message, the documents its question was limited to
  ([{id, filename, file_type, drive_type}]). Shown on the message and used to understand follow-ups.

Revision ID: 0005_message_documents
Revises: 0004_chat_questions
Create Date: 2026-11-01
"""
from alembic import op

revision = "0005_message_documents"
down_revision = "0004_chat_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS documents json")


def downgrade() -> None:
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS documents")
