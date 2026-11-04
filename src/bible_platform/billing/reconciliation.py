"""Controlled durable observation of sandbox billing events (proposal-only).

This is NOT a background worker, API endpoint, checkout processor, or a
premium entitlement controller. Disabled outside local SQLite and synthetic
MockTransport. Trusted expectations always come from the Bible database.

No Asaas real/sandbox network calls, no writes to entitlements, and no payment
capture are permitted in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from bible_platform.config import get_settings
from .asaas_adapter import AsaasClient, ProviderError
from .provider_verification import verify_payment_readonly, _validated_id
from .schema import checkouts, entitlements, reconciliation_attempts, webhook_events


class ReconciliationBlocked(RuntimeError):
    """Fail-closed code safe for diagnostics without secrets."""


@dataclass(frozen=True)
class ReconciliationResult:
    event_id: str
    processing_status: str
    replayed: bool
    entitlement_changed: bool = False


def _checkout_for_event(conn, event_id: str):
    event = conn.execute(
        select(webhook_events).where(webhook_events.c.event_id == event_id)
    ).mappings().first()
    if event is None:
        raise ReconciliationBlocked("BIBLE_RECONCILIATION_EVENT_NOT_FOUND")
    # Never take plan, amount, provider ref, actor, or checkout id from a
    # webhook payload, HTTP request or caller.
    checkout = conn.execute(select(checkouts).where(and_(
        checkouts.c.tenant_id == event["tenant_id"],
        checkouts.c.checkout_id == event["checkout_id"],
        checkouts.c.user_id == event["user_id"],
    ))).mappings().first()
    if checkout is None or not checkout["provider_ref"] or (
            checkout["provider_ref"] != event["provider_ref"]):
        raise ReconciliationBlocked("BIBLE_RECONCILIATION_CHECKOUT_MISMATCH")
    if checkout["status"] not in {"pending", "created", "awaiting_payment"}:
        raise ReconciliationBlocked("BIBLE_RECONCILIATION_CHECKOUT_STATE_UNSUPPORTED")
    return event, checkout


def _append_attempt(conn, event, *, result: str,
                    provider_status: str | None = None,
                    error_code: str | None = None):
    conn.execute(insert(reconciliation_attempts).values(
        attempt_id="ra_" + uuid4().hex,
        event_id=event["event_id"],
        tenant_id=event["tenant_id"],
        user_id=event["user_id"],
        checkout_id=event["checkout_id"],
        payment_id=event["payment_id"],
        result=result,
        provider_status=provider_status,
        error_code=error_code,
        created_at=datetime.now(timezone.utc),
    ))


def reconcile_event_local_readonly(
    *,
    engine: Engine,
    client: AsaasClient,
    event_id: str,
) -> ReconciliationResult:
    """Manually process one persisted *synthetic* event in local SQLite.

    A provider observation is recorded as a durable audit event and inbox
    processing status, never as permission for a paid subscription.
    No automatic retries or live provider calls are made.
    """
    if get_settings().environment != "local":
        raise ReconciliationBlocked("BIBLE_RECONCILIATION_LOCAL_ONLY")
    if engine.dialect.name != "sqlite":
        raise ReconciliationBlocked("BIBLE_RECONCILIATION_POSTGRES_RLS_NOT_PROVEN")
    if client.base_url != "https://api-sandbox.asaas.com/v3" or not isinstance(
            client.transport, httpx.MockTransport):
        raise ReconciliationBlocked("BIBLE_RECONCILIATION_MOCK_TRANSPORT_REQUIRED")
    event_id = _validated_id(event_id)

    try:
        with engine.connect() as conn:
            event, checkout = _checkout_for_event(conn, event_id)
            if event["processing_status"] != "pending_reconciliation":
                return ReconciliationResult(event_id, event["processing_status"], True)
            # Snapshot from Bible-owned checkout; never use payload as expected facts.
            expected = dict(
                payment_id=event["payment_id"],
                checkout_id=checkout["checkout_id"],
                provider_ref=checkout["provider_ref"],
                amount_cents=checkout["amount_cents"],
            )
    except SQLAlchemyError as exc:
        raise ReconciliationBlocked("BIBLE_RECONCILIATION_STORAGE_UNAVAILABLE") from exc

    observation = None
    error = None
    try:
        observation = verify_payment_readonly(client, **expected)
    except ProviderError as exc:
        error = exc

    if error is None:
        status = {
            "paid_observed_unapproved": "observed_paid_hold",
            "payment_pending": "observed_pending",
            "reversal_or_dispute_hold": "observed_reversal_hold",
            "manual_review": "observed_manual_review",
        }[observation.assessment]
        # A reversal webhook whose provider still reports paid is held for
        # manual review rather than attempting any privilege transition.
        if any(part in event["event_type"] for part in ("REFUND", "CHARGEBACK"))                 and status == "observed_paid_hold":
            status = "observed_manual_review"
    else:
        status = (
            "pending_reconciliation" if error.outcome_unknown
            else "held_provider_mismatch"
        )

    try:
        with engine.begin() as conn:
            current_event, current_checkout = _checkout_for_event(conn, event_id)
            if current_event["processing_status"] != "pending_reconciliation":
                return ReconciliationResult(
                    event_id, current_event["processing_status"], True
                )
            # Guard against trusted checkout changing during provider I/O.
            if (current_event["payment_id"] != expected["payment_id"]
                    or current_checkout["checkout_id"] != expected["checkout_id"]
                    or current_checkout["provider_ref"] != expected["provider_ref"]
                    or current_checkout["amount_cents"] != expected["amount_cents"]):
                raise ReconciliationBlocked("BIBLE_RECONCILIATION_SNAPSHOT_CHANGED")

            if status != "pending_reconciliation":
                updated = conn.execute(update(webhook_events).where(and_(
                    webhook_events.c.event_id == event_id,
                    webhook_events.c.processing_status == "pending_reconciliation",
                )).values(processing_status=status))
                if updated.rowcount != 1:
                    raise ReconciliationBlocked("BIBLE_RECONCILIATION_CONCURRENT_CONFLICT")
            _append_attempt(
                conn, current_event, result=(
                    "retry_required" if status == "pending_reconciliation" else status
                ), provider_status=(
                    observation.provider_status if observation is not None else None
                ), error_code=(error.code if error is not None else None)
            )
    except SQLAlchemyError as exc:
        raise ReconciliationBlocked("BIBLE_RECONCILIATION_STORAGE_UNAVAILABLE") from exc

    # No writes/reads to 'entitlements': enforced by code and regression tests.
    return ReconciliationResult(event_id, status, False)
