"""Isolated billing foundation (no automatic paid activation).

Revision ID: 005_bible_billing_foundation
Revises: 004_bible_bookmarks_highlights
"""
from alembic import op
import sqlalchemy as sa

revision = "005_bible_billing_foundation"
down_revision = "004_bible_bookmarks_highlights"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "bible_billing_checkouts",
        sa.Column("tenant_id", sa.String(120), nullable=False),
        sa.Column("checkout_id", sa.String(120), nullable=False),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("plan_code", sa.String(60), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("provider_ref", sa.String(160), nullable=True),
        sa.Column("provider_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "checkout_id"),
        sa.UniqueConstraint("tenant_id", "user_id", "idempotency_key", name="uq_bible_billing_idempotency"),
    )
    op.create_index("ix_bible_billing_checkout_owner", "bible_billing_checkouts", ["tenant_id", "user_id"])
    op.create_table(
        "bible_billing_entitlements",
        sa.Column("tenant_id", sa.String(120), nullable=False),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("plan_code", sa.String(60), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_checkout_id", sa.String(120), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "user_id"),
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in ("bible_billing_checkouts", "bible_billing_entitlements"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY {table}_actor_scope ON {table} "
                "USING (tenant_id = current_setting('app.tenant_id', true) "
                "AND user_id = current_setting('app.user_id', true)) "
                "WITH CHECK (tenant_id = current_setting('app.tenant_id', true) "
                "AND user_id = current_setting('app.user_id', true))"
            )

def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        for table in ("bible_billing_entitlements", "bible_billing_checkouts"):
            op.execute(f"DROP POLICY IF EXISTS {table}_actor_scope ON {table}")
    op.drop_table("bible_billing_entitlements")
    op.drop_index("ix_bible_billing_checkout_owner", table_name="bible_billing_checkouts")
    op.drop_table("bible_billing_checkouts")
