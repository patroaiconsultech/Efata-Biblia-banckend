"""Local-only reconciliation audit ledger; never changes premium entitlements.

Revision ID: 007_bible_billing_reconciliation_audit
Revises: 006_bible_billing_webhook_inbox
"""
from alembic import op
import sqlalchemy as sa

revision = "007_bible_billing_reconciliation_audit"
down_revision = "006_bible_billing_webhook_inbox"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bible_billing_reconciliation_attempts",
        sa.Column("attempt_id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(160), nullable=False),
        sa.Column("tenant_id", sa.String(120), nullable=False),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("checkout_id", sa.String(120), nullable=False),
        sa.Column("payment_id", sa.String(160), nullable=False),
        sa.Column("result", sa.String(60), nullable=False),
        sa.Column("provider_status", sa.String(100), nullable=True),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_bible_reconciliation_event",
        "bible_billing_reconciliation_attempts",
        ["tenant_id", "user_id", "event_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        table = "bible_billing_reconciliation_attempts"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # A dedicated worker role and its cross-tenant access model are NOT
        # approved. Only actor-scope policies are declared in this proposal.
        op.execute(
            f"CREATE POLICY {table}_actor_scope ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true) "
            "AND user_id = current_setting('app.user_id', true)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true) "
            "AND user_id = current_setting('app.user_id', true))"
        )


def downgrade():
    # Destructive; NOT approved against data-bearing databases.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS "
                   "bible_billing_reconciliation_attempts_actor_scope "
                   "ON bible_billing_reconciliation_attempts")
    op.drop_index("ix_bible_reconciliation_event",
                  table_name="bible_billing_reconciliation_attempts")
    op.drop_table("bible_billing_reconciliation_attempts")
