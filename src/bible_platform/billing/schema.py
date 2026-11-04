"""Bible-owned billing persistence; independent of ORKIO financial tables."""
from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Integer, String, Table, Text, UniqueConstraint
from bible_platform.infrastructure.schema import metadata

checkouts = Table(
    "bible_billing_checkouts", metadata,
    Column("tenant_id", String(120), primary_key=True),
    Column("checkout_id", String(120), primary_key=True),
    Column("user_id", String(120), nullable=False),
    Column("plan_code", String(60), nullable=False),
    Column("amount_cents", Integer, nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("status", String(30), nullable=False),
    Column("provider_ref", String(160)),
    Column("provider_url", Text()),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "user_id", "idempotency_key", name="uq_bible_billing_idempotency"),
)
entitlements = Table(
    "bible_billing_entitlements", metadata,
    Column("tenant_id", String(120), primary_key=True),
    Column("user_id", String(120), primary_key=True),
    Column("plan_code", String(60), nullable=False),
    Column("valid_until", DateTime(timezone=True), nullable=False),
    Column("source_checkout_id", String(120), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


# Event inbox only: records authenticated sandbox notifications. No paid entitlement
# is granted from an incoming webhook. Global event_id makes redelivery idempotent.
webhook_events = Table(
    "bible_billing_webhook_events", metadata,
    Column("event_id", String(160), primary_key=True),
    Column("tenant_id", String(120), nullable=False),
    Column("user_id", String(120), nullable=False),
    Column("checkout_id", String(120), nullable=False),
    Column("event_type", String(90), nullable=False),
    Column("payment_id", String(160), nullable=False),
    Column("provider_ref", String(160), nullable=False),
    Column("processing_status", String(40), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)


# Append-only ledger of synthetic provider GET observations. This does NOT
# represent settlement or permission to change billing entitlements.
reconciliation_attempts = Table(
    "bible_billing_reconciliation_attempts", metadata,
    Column("attempt_id", String(64), primary_key=True),
    Column("event_id", String(160), nullable=False),
    Column("tenant_id", String(120), nullable=False),
    Column("user_id", String(120), nullable=False),
    Column("checkout_id", String(120), nullable=False),
    Column("payment_id", String(160), nullable=False),
    Column("result", String(60), nullable=False),
    Column("provider_status", String(100)),
    Column("error_code", String(120)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
