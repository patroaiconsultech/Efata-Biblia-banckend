"""Independent Bible native authentication tables.

Revision ID: 009_bible_native_auth
Revises: 008_bible_faith_preferences
No runtime migration is executed by creating this file.
"""
from alembic import op
import sqlalchemy as sa

revision = "009_bible_native_auth"
down_revision = "008_bible_faith_preferences"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bible_auth_users",
        sa.Column("user_id", sa.String(120), primary_key=True),
        sa.Column("tenant_id", sa.String(120), nullable=False, unique=True),
        sa.Column("email_normalized", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "bible_auth_sessions",
        sa.Column("session_digest", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("tenant_id", sa.String(120), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["bible_auth_users.user_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_bible_auth_sessions_owner", "bible_auth_sessions", ["user_id", "tenant_id"])
    op.create_table(
        "bible_auth_login_attempts",
        sa.Column("attempt_id", sa.String(64), primary_key=True),
        sa.Column("attempt_key", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bible_auth_attempt_key_time", "bible_auth_login_attempts", ["attempt_key", "created_at"])
    op.create_table(
        "bible_auth_reset_tokens",
        sa.Column("token_digest", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["bible_auth_users.user_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_bible_auth_reset_owner", "bible_auth_reset_tokens", ["user_id"])
    if op.get_bind().dialect.name == "postgresql":
        # Pre-authentication credential lookup necessarily precedes tenant scope.
        # These tables are a restricted control plane, never client-accessible.
        for table in ("bible_auth_users", "bible_auth_sessions", "bible_auth_login_attempts", "bible_auth_reset_tokens"):
            op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")


def downgrade():
    # DESTRUCTIVE: never apply without AO-01 approval, export and recovery rehearsal.
    op.drop_index("ix_bible_auth_reset_owner", table_name="bible_auth_reset_tokens")
    op.drop_table("bible_auth_reset_tokens")
    op.drop_index("ix_bible_auth_attempt_key_time", table_name="bible_auth_login_attempts")
    op.drop_table("bible_auth_login_attempts")
    op.drop_index("ix_bible_auth_sessions_owner", table_name="bible_auth_sessions")
    op.drop_table("bible_auth_sessions")
    op.drop_table("bible_auth_users")
