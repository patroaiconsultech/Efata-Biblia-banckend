"""Shared AI pilot budget and numeric usage ledger. NO entitlement changes.

Revision ID: 010_bible_ai_budget
Revises: 009_bible_native_auth

Precondition: dedicated PostgreSQL DB and audited migration-role credentials.
No runtime CREATE TABLE; application fails closed until both tables AND
explicit bible_app grants are in place. No user prompts/answers/keys stored.
"""
from alembic import op
import sqlalchemy as sa

revision = "010_bible_ai_budget"
down_revision = "009_bible_native_auth"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite schema creation is permitted for LOCAL migration regression tests
    # only. The paid AI runtime strictly requires PostgreSQL (see budget.py).
    op.create_table(
        "bible_ai_budget_days",
        sa.Column("day_utc", sa.Date(), primary_key=True),
        sa.Column("reserved_micro_usd", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reserved_micro_usd >= 0", name="ck_bible_ai_days_nonnegative"),
        sa.CheckConstraint("requests >= 0", name="ck_bible_ai_days_requests"),
    )
    op.create_table(
        "bible_ai_budget_requests",
        sa.Column("attempt_id", sa.String(64), primary_key=True),
        sa.Column("day_utc", sa.Date(), nullable=False),
        sa.Column("tenant_id", sa.String(120), nullable=False),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("request_id_digest", sa.String(64), nullable=False),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("reserved_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_micro_usd", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["day_utc"], ["bible_ai_budget_days.day_utc"]),
        sa.UniqueConstraint("tenant_id", "user_id", "request_id_digest",
                            name="uq_bible_ai_request_actor_idempotency"),
        sa.CheckConstraint("reserved_micro_usd > 0", name="ck_bible_ai_request_reserved"),
        sa.CheckConstraint("status IN ('reserved', 'completed', 'unmetered', 'unknown')",
                           name="ck_bible_ai_request_status"),
    )
    op.create_index("ix_bible_ai_request_day_actor",
                    "bible_ai_budget_requests", ["day_utc", "tenant_id", "user_id"])
    # Global finance counters cannot be tenant-RLS scoped; these tables are
    # internal control plane, never exposed by API listing or to the browser.
    # The SQL contract uses a single global FOR UPDATE row as the cross-replica
    # serialization point. The app role MUST be non-superuser, NOBYPASSRLS.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("REVOKE ALL ON public.bible_ai_budget_days FROM PUBLIC")
        op.execute("REVOKE ALL ON public.bible_ai_budget_requests FROM PUBLIC")
    # Deliberately NO automatic GRANT to a guessed role:
    # approve/apply grants separately for the real API DB role.


def downgrade():
    # Deleting a financial ledger erases cost/idempotency evidence and can
    # silently reauthorize calls. No automated destructive downgrade.
    raise RuntimeError("BIBLE_AI_BUDGET_DOWNGRADE_REQUIRES_AO01_RECOVERY_PLAN")
