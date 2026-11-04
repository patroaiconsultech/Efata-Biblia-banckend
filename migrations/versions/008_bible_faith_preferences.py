"""Voluntary encrypted faith preferences. No default or implicit profiling.

Revision ID: 008_bible_faith_preferences
Revises: 007_bible_billing_reconciliation_audit
"""
from alembic import op
import sqlalchemy as sa

revision = "008_bible_faith_preferences"
down_revision = "007_bible_billing_reconciliation_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bible_faith_preferences",
        sa.Column("tenant_id", sa.String(120), primary_key=True),
        sa.Column("user_id", sa.String(120), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(20), nullable=False),
        sa.Column("consent_version", sa.String(60), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE bible_faith_preferences ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE bible_faith_preferences FORCE ROW LEVEL SECURITY")
        op.execute(
            "CREATE POLICY bible_faith_preferences_actor_scope ON bible_faith_preferences "
            "USING (tenant_id = current_setting('app.tenant_id', true) "
            "AND user_id = current_setting('app.user_id', true)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true) "
            "AND user_id = current_setting('app.user_id', true))"
        )


def downgrade():
    # Destructive: requires a reviewed export, approval and restoration plan.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS bible_faith_preferences_actor_scope ON bible_faith_preferences")
    op.drop_table("bible_faith_preferences")
