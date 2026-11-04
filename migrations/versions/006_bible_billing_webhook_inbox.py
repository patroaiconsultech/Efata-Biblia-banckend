"""Sandbox-only webhook inbox, without payment activation or entitlement grants.

Revision ID: 006_bible_billing_webhook_inbox
Revises: 005_bible_billing_foundation
"""
from alembic import op
import sqlalchemy as sa

revision = "006_bible_billing_webhook_inbox"
down_revision = "005_bible_billing_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bible_billing_webhook_events",
        sa.Column("event_id", sa.String(160), primary_key=True),
        sa.Column("tenant_id", sa.String(120), nullable=False),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("checkout_id", sa.String(120), nullable=False),
        sa.Column("event_type", sa.String(90), nullable=False),
        sa.Column("payment_id", sa.String(160), nullable=False),
        sa.Column("provider_ref", sa.String(160), nullable=False),
        sa.Column("processing_status", sa.String(40), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_bible_webhook_checkout", "bible_billing_webhook_events",
        ["tenant_id", "user_id", "checkout_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        table = "bible_billing_webhook_events"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # Existing actor isolation. This inbox is NOT enabled in staging/production
        # until a dedicated, least-privilege webhook role is designed and tested.
        op.execute(
            f"CREATE POLICY {table}_actor_scope ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true) "
            "AND user_id = current_setting('app.user_id', true)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true) "
            "AND user_id = current_setting('app.user_id', true))"
        )


def downgrade():
    # Destructive: never run against real billing event history without approval
    # and backup/restore plan.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS bible_billing_webhook_events_actor_scope "
                   "ON bible_billing_webhook_events")
    op.drop_index("ix_bible_webhook_checkout", table_name="bible_billing_webhook_events")
    op.drop_table("bible_billing_webhook_events")
