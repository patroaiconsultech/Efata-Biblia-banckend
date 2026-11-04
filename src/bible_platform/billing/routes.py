"""Safe Bible billing API: catalog + actual actor-scoped entitlements.

Checkout activation and provider webhooks intentionally fail closed pending
provider settlement/reconciliation, RLS testing and explicit human approval.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError

from bible_platform.api.dependencies import get_principal
from bible_platform.domain_models import BiblePrincipal
from bible_platform.infrastructure.store import store, _set_rls_context
from .catalog import CatalogError, plans
from .sandbox_checkout import CheckoutRequest, create_sandbox_checkout, sandbox_checkout_ready
from .schema import entitlements

router = APIRouter(prefix="/api/billing", tags=["billing"])
Principal = Annotated[BiblePrincipal, Depends(get_principal)]

@router.get("/plans")
def list_plans():
    try:
        available = plans()
    except CatalogError as exc:
        raise HTTPException(503, detail={"code": str(exc), "message": "Catálogo de planos indisponível."}) from exc
    return {"free_access": True, "items": available, "checkout_enabled": sandbox_checkout_ready(),
            "provider": "asaas_sandbox_only",
            "notice": "Somente checkout sandbox local sob habilitação explícita. Não há cobrança comercial."}

@router.get("/me")
def billing_me(principal: Principal):
    # Never derive paid access from frontend state, user headers or a plan selection.
    if store.engine is None:
        return {"plan_code": "free", "active": True, "source": "free_default", "paid_access": False}
    try:
        with store.engine.begin() as conn:
            _set_rls_context(conn, principal.tenant_id, principal.user_id)
            row = conn.execute(select(entitlements).where(
                and_(entitlements.c.tenant_id == principal.tenant_id,
                     entitlements.c.user_id == principal.user_id)
            )).mappings().first()
    except SQLAlchemyError as exc:
        raise HTTPException(503, detail={"code": "BIBLE_BILLING_SCHEMA_NOT_READY",
                                         "message": "Migração do billing pendente."}) from exc
    if row is None:
        return {"plan_code": "free", "active": True, "source": "free_default", "paid_access": False}
    expiration = row["valid_until"]
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=timezone.utc)
    if expiration <= datetime.now(timezone.utc):
        return {"plan_code": "free", "active": True, "source": "free_default", "paid_access": False}
    return {"plan_code": row["plan_code"], "active": True, "source": "verified_persisted_entitlement",
            "paid_access": True, "valid_until": expiration.isoformat()}


@router.post("/checkouts")
def create_checkout(body: CheckoutRequest, principal: Principal):
    """Sandbox-only checkout; live billing remains blocked by design."""
    return create_sandbox_checkout(body=body, principal=principal)
