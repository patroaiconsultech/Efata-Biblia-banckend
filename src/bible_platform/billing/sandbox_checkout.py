"""Fail-closed Asaas sandbox checkout with durable intent and idempotency.

NOT production billing, does not grant entitlements. Must be explicitly enabled
in a local SQLite sandbox. Never call the provider during a database transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os
import re
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from bible_platform.config import env_bool, get_settings
from bible_platform.domain_models import BiblePrincipal
from bible_platform.infrastructure.store import store
from .asaas_adapter import AsaasClient, ProviderError
from .catalog import CatalogError, find_plan
from .schema import checkouts

_KEY = re.compile(r"^[A-Za-z0-9_-]{12,128}$")
_ALLOWED_URL_HOSTS = {"asaas.com", "www.asaas.com", "sandbox.asaas.com", "www.sandbox.asaas.com"}


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    idempotency_key: str = Field(min_length=12, max_length=128)


def _reject(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code})


def _provider() -> AsaasClient:
    key = os.getenv("BIBLE_BILLING_ASAAS_SANDBOX_KEY", "").strip()
    if len(key) < 16:
        raise _reject(503, "BIBLE_BILLING_SANDBOX_CREDENTIALS_REQUIRED")
    return AsaasClient(api_key=key, environment="sandbox")


def _url_ok(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in _ALLOWED_URL_HOSTS and not parsed.username and not parsed.password
    except ValueError:
        return False


def sandbox_checkout_ready() -> bool:
    """Advertise checkout only when ALL local sandbox prerequisites are present."""
    settings = get_settings()
    if settings.environment != "local" or not env_bool("BIBLE_BILLING_SANDBOX_CHECKOUT_ENABLED", False):
        return False
    if store.engine is None or store.engine.dialect.name != "sqlite":
        return False
    if len(os.getenv("BIBLE_BILLING_ASAAS_SANDBOX_KEY", "").strip()) < 16:
        return False
    url = urlparse(os.getenv("BIBLE_BILLING_SANDBOX_SUCCESS_URL", ""))
    return url.scheme == "https" and bool(url.netloc) and not url.username and not url.password


def create_sandbox_checkout(*, body: CheckoutRequest, principal: BiblePrincipal) -> dict:
    settings = get_settings()
    if settings.environment != "local" or not env_bool("BIBLE_BILLING_SANDBOX_CHECKOUT_ENABLED", False):
        raise _reject(503, "BIBLE_BILLING_CHECKOUTS_NOT_ACTIVATED")
    # The stored intent is only supported with a single-node local SQLite DB.
    # PostgreSQL distributed concurrency, payment lifecycle and RLS remain unapproved.
    if store.engine is None or store.engine.dialect.name != "sqlite":
        raise _reject(503, "BIBLE_BILLING_SANDBOX_DATABASE_REQUIRED")
    if not _KEY.fullmatch(body.idempotency_key):
        raise _reject(422, "BIBLE_BILLING_IDEMPOTENCY_INVALID")
    try:
        plan = find_plan(body.plan_code)
    except CatalogError as exc:
        raise _reject(503, str(exc)) from exc
    if not plan:
        raise _reject(422, "BIBLE_BILLING_PLAN_NOT_FOUND")
    success_url = os.getenv("BIBLE_BILLING_SANDBOX_SUCCESS_URL", "")
    u = urlparse(success_url)
    if u.scheme != "https" or not u.netloc or u.username or u.password:
        raise _reject(503, "BIBLE_BILLING_CALLBACK_NOT_CONFIGURED")

    provider = _provider()
    amount = int(Decimal(plan["price_brl"]) * 100)
    scoped = and_(checkouts.c.tenant_id == principal.tenant_id,
                  checkouts.c.user_id == principal.user_id,
                  checkouts.c.idempotency_key == body.idempotency_key)
    try:
        with store.engine.begin() as conn:
            existing = conn.execute(select(checkouts).where(scoped)).mappings().first()
            if existing is not None:
                if existing["plan_code"] != body.plan_code or existing["amount_cents"] != amount:
                    raise _reject(409, "BIBLE_BILLING_IDEMPOTENCY_CONFLICT")
                if existing["status"] == "awaiting_payment" and existing["provider_url"]:
                    return {"checkout_id": existing["checkout_id"], "status": existing["status"],
                            "payment_url": existing["provider_url"], "replayed": True}
                raise _reject(409, "BIBLE_BILLING_INTENT_ALREADY_CREATED")
            checkout_id = "bc_" + uuid4().hex
            conn.execute(insert(checkouts).values(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                checkout_id=checkout_id,
                plan_code=body.plan_code,
                amount_cents=amount,
                idempotency_key=body.idempotency_key,
                status="creating",
                provider_ref=None,
                provider_url=None,
                created_at=datetime.now(timezone.utc),
            ))
    except IntegrityError as exc:
        raise _reject(409, "BIBLE_BILLING_IDEMPOTENCY_RACE") from exc
    except SQLAlchemyError as exc:
        raise _reject(503, "BIBLE_BILLING_STORAGE_UNAVAILABLE") from exc

    try:
        result = provider.create_payment_link(
            checkout_id=checkout_id, plan=plan, success_url=success_url,
        )
        if not _url_ok(result["payment_url"]):
            raise ProviderError("BIBLE_BILLING_PROVIDER_UNSAFE_URL", True)
    except (ProviderError, HTTPException) as exc:
        # Never retry blindly: a provider timeout/5xx may have created a link.
        try:
            with store.engine.begin() as conn:
                conn.execute(update(checkouts).where(and_(
                    checkouts.c.tenant_id == principal.tenant_id,
                    checkouts.c.checkout_id == checkout_id,
                    checkouts.c.status == "creating",
                )).values(status=("outcome_unknown" if isinstance(exc, HTTPException) or exc.outcome_unknown else "provider_rejected")))
        except SQLAlchemyError:
            pass  # No success response when persistence fails.
        if isinstance(exc, HTTPException):
            raise
        raise _reject(503, exc.code) from exc

    try:
        with store.engine.begin() as conn:
            updated = conn.execute(update(checkouts).where(and_(
                checkouts.c.tenant_id == principal.tenant_id,
                checkouts.c.checkout_id == checkout_id,
                checkouts.c.user_id == principal.user_id,
                checkouts.c.status == "creating",
            )).values(status="awaiting_payment",
                       provider_ref=result["provider_id"],
                       provider_url=result["payment_url"]))
            if updated.rowcount != 1:
                raise _reject(503, "BIBLE_BILLING_CHECKOUT_STATE_CONFLICT")
    except SQLAlchemyError as exc:
        raise _reject(503, "BIBLE_BILLING_STORAGE_UNAVAILABLE") from exc
    return {"checkout_id": checkout_id, "status": "awaiting_payment",
            "payment_url": result["payment_url"], "replayed": False}
