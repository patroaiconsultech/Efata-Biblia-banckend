"""Guarded sandbox webhook inbox for Asaas charge events.

Only authorized *local* test events are recorded. No capture, refund, checkout
activation or entitlement grant is performed. Never treat a webhook body as
proof of payment without a separately reconciled provider resource.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hmac
import json
import os
import re

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from bible_platform.config import env_bool, get_settings
from bible_platform.infrastructure.store import store
from .schema import checkouts, webhook_events

router = APIRouter(prefix="/api/billing/webhooks", tags=["billing"])
_ID_RE = re.compile(r"^[a-zA-Z0-9_.:&-]{3,160}$")
_ALLOWED_EVENTS = frozenset({
    "PAYMENT_CREATED", "PAYMENT_UPDATED", "PAYMENT_AUTHORIZED",
    "PAYMENT_CONFIRMED", "PAYMENT_RECEIVED", "PAYMENT_OVERDUE",
    "PAYMENT_DELETED", "PAYMENT_REFUNDED", "PAYMENT_REFUND_IN_PROGRESS",
    "PAYMENT_REFUND_DENIED", "PAYMENT_CHARGEBACK_REQUESTED",
    "PAYMENT_CHARGEBACK_DISPUTE", "PAYMENT_AWAITING_CHARGEBACK_REVERSAL",
    "PAYMENT_RECEIVED_IN_CASH_UNDONE",
})
_BODY_LIMIT = 16 * 1024


def _reject(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code})


async def _small_json(request: Request) -> dict:
    try:
        stated = int(request.headers.get("content-length", "0"))
        if stated > _BODY_LIMIT:
            raise _reject(413, "BIBLE_WEBHOOK_BODY_TOO_LARGE")
    except ValueError:
        raise _reject(400, "BIBLE_WEBHOOK_CONTENT_LENGTH_INVALID")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _BODY_LIMIT:
            raise _reject(413, "BIBLE_WEBHOOK_BODY_TOO_LARGE")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise _reject(400, "BIBLE_WEBHOOK_JSON_INVALID")
    if not isinstance(payload, dict):
        raise _reject(400, "BIBLE_WEBHOOK_JSON_INVALID")
    return payload


def _id(value: object) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise _reject(422, "BIBLE_WEBHOOK_ID_INVALID")
    return value


def _event_details(payload: dict) -> tuple[str, str, str, str, str, int]:
    event_id = _id(payload.get("id"))
    event_type = payload.get("event")
    if not isinstance(event_type, str) or event_type not in _ALLOWED_EVENTS:
        raise _reject(422, "BIBLE_WEBHOOK_EVENT_INVALID")
    payment = payload.get("payment")
    if not isinstance(payment, dict):
        raise _reject(422, "BIBLE_WEBHOOK_PAYMENT_INVALID")
    payment_id = _id(payment.get("id"))
    link = payment.get("paymentLink")
    if isinstance(link, int):
        link = str(link)
    provider_ref = _id(link)
    # Sandbox policy: checkout reference is mandatory. Missing references
    # must not be silently accepted using link+amount alone. Real provider
    # webhooks require authenticated reconciliation before activation.
    external_ref = payment.get("externalReference")
    if external_ref is None:
        raise _reject(422, "BIBLE_WEBHOOK_CHECKOUT_REFERENCE_REQUIRED")
    external_ref = _id(external_ref)
    try:
        amount = Decimal(str(payment["value"]))
        if not amount.is_finite() or amount <= 0 or amount > 10000 or (amount * 100) != (amount * 100).to_integral_exact():
            raise InvalidOperation
        cents = int(amount * 100)
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise _reject(422, "BIBLE_WEBHOOK_AMOUNT_INVALID")
    return event_id, event_type, payment_id, provider_ref, external_ref, cents


@router.post("/asaas")
async def asaas_sandbox_webhook(request: Request):
    settings = get_settings()
    if settings.environment != "local" or not env_bool("BIBLE_BILLING_WEBHOOK_SANDBOX_ENABLED", False):
        raise _reject(503, "BIBLE_WEBHOOK_NOT_ACTIVATED")
    secret = os.getenv("BIBLE_BILLING_WEBHOOK_TOKEN", "")
    if len(secret) < 32:
        raise _reject(503, "BIBLE_WEBHOOK_SECRET_NOT_CONFIGURED")
    supplied = request.headers.get("asaas-access-token", "")
    if not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
        raise _reject(401, "BIBLE_WEBHOOK_AUTH_FAILED")
    if store.engine is None:
        raise _reject(503, "BIBLE_WEBHOOK_DATABASE_REQUIRED")
    payload = await _small_json(request)
    event_id, event_type, payment_id, provider_ref, external_ref, cents = _event_details(payload)

    # A link ID is looked up only in the Bible-owned checkout table. Tenant/user
    # from event JSON or browser headers are never accepted as authority.
    try:
        with store.engine.begin() as conn:
            matches = conn.execute(
                select(checkouts).where(checkouts.c.provider_ref == provider_ref).limit(2)
            ).mappings().all()
            if len(matches) != 1:
                raise _reject(503, "BIBLE_WEBHOOK_CHECKOUT_NOT_VERIFIED")
            checkout = matches[0]
            if external_ref != checkout["checkout_id"]:
                raise _reject(422, "BIBLE_WEBHOOK_CHECKOUT_REFERENCE_MISMATCH")
            if cents != checkout["amount_cents"]:
                raise _reject(422, "BIBLE_WEBHOOK_AMOUNT_MISMATCH")
            conn.execute(insert(webhook_events).values(
                event_id=event_id,
                tenant_id=checkout["tenant_id"],
                user_id=checkout["user_id"],
                checkout_id=checkout["checkout_id"],
                event_type=event_type,
                payment_id=payment_id,
                provider_ref=provider_ref,
                processing_status="pending_reconciliation",
                received_at=datetime.now(timezone.utc),
            ))
    except IntegrityError as exc:
        # Only acknowledge confirmed re-delivery of the *same* durable event.
        # Other constraint failures must cause retry, not false HTTP 200.
        try:
            with store.engine.connect() as conn:
                existing = conn.execute(select(webhook_events).where(
                    webhook_events.c.event_id == event_id
                )).mappings().first()
        except SQLAlchemyError as lookup_exc:
            raise _reject(503, "BIBLE_WEBHOOK_STORAGE_UNAVAILABLE") from lookup_exc
        if existing is None:
            raise _reject(503, "BIBLE_WEBHOOK_STORAGE_UNAVAILABLE") from exc
        if (existing["checkout_id"] != checkout["checkout_id"]
                or existing["event_type"] != event_type
                or existing["payment_id"] != payment_id
                or existing["provider_ref"] != provider_ref):
            raise _reject(409, "BIBLE_WEBHOOK_EVENT_ID_COLLISION")
        return {"received": True, "duplicate": True, "processed": False}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise _reject(503, "BIBLE_WEBHOOK_STORAGE_UNAVAILABLE") from exc
    return {"received": True, "duplicate": False, "processed": False}
