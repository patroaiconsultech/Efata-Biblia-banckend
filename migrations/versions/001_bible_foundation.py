"""bible foundation

Revision ID: 001_bible_foundation
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "001_bible_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bible_threads",
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("thread_id", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "thread_id"),
    )
    op.create_index("ix_bible_threads_owner", "bible_threads", ["tenant_id", "user_id"])
    op.create_table(
        "bible_messages",
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("message_id", sa.String(length=120), nullable=False),
        sa.Column("thread_id", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("blocks_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "message_id"),
    )
    op.create_index("ix_bible_messages_thread_owner", "bible_messages", ["tenant_id", "thread_id", "user_id"])
    op.create_table(
        "bible_progress",
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("text_unit_id", sa.String(length=160), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", "text_unit_id"),
    )
    op.create_table(
        "bible_personal_entries",
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("entry_id", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "entry_id"),
    )
    op.create_index("ix_bible_personal_owner", "bible_personal_entries", ["tenant_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_bible_personal_owner", table_name="bible_personal_entries")
    op.drop_table("bible_personal_entries")
    op.drop_table("bible_progress")
    op.drop_index("ix_bible_messages_thread_owner", table_name="bible_messages")
    op.drop_table("bible_messages")
    op.drop_index("ix_bible_threads_owner", table_name="bible_threads")
    op.drop_table("bible_threads")
