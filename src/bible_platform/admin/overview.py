"""RC 0.14: truthful, tenant-scoped admin snapshot from persisted local SQLite data.

No private content, faith profile, plaintext diary, or personal records are queried.
No cross-tenant service role is introduced. PostgreSQL requires separately approved RLS.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select, union
from sqlalchemy.exc import SQLAlchemyError

from bible_platform.api.dependencies import get_principal, require_admin
from bible_platform.config import get_settings
from bible_platform.domain_models import BiblePrincipal
from bible_platform.infrastructure.store import store
from bible_platform.infrastructure.schema import (
    threads_table, messages_table, progress_table, bookmarks_table,
)
from bible_platform.billing.schema import (
    checkouts, entitlements, reconciliation_attempts,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
Principal = Annotated[BiblePrincipal, Depends(get_principal)]


def _count(connection, table, tenant: str, *filters) -> int:
    statement = select(func.count()).select_from(table).where(table.c.tenant_id == tenant, *filters)
    return int(connection.execute(statement).scalar_one())


def _daily_message_counts(connection, tenant: str, now: datetime) -> list[dict[str, object]]:
    start = now.date() - timedelta(days=6)
    rows = connection.execute(
        select(func.date(messages_table.c.created_at).label("day"), func.count().label("events"))
        .where(messages_table.c.tenant_id == tenant,
               messages_table.c.created_at >= datetime.combine(start, datetime.min.time(), timezone.utc))
        .group_by(func.date(messages_table.c.created_at))
    ).all()
    counts = {str(day): int(events) for day, events in rows}
    return [{"date": (start + timedelta(days=i)).isoformat(),
             "messages": counts.get((start + timedelta(days=i)).isoformat(), 0)} for i in range(7)]


def snapshot_for_tenant(engine, tenant_id: str) -> dict[str, object]:
    """Read a single tenant's persisted aggregates, never other tenants' data."""
    now = datetime.now(timezone.utc)
    with engine.connect() as connection:
        observed = union(*[
            select(table.c.user_id.label("user_id")).where(table.c.tenant_id == tenant_id)
            for table in (threads_table, messages_table, progress_table, bookmarks_table, checkouts, entitlements)
        ]).subquery()
        users_with_activity = int(connection.execute(select(func.count()).select_from(observed)).scalar_one())
        summary = {
            "users_with_persisted_activity": users_with_activity,
            "conversations": _count(connection, threads_table, tenant_id),
            "messages": _count(connection, messages_table, tenant_id),
            "completed_readings": _count(connection, progress_table, tenant_id, progress_table.c.completed.is_(True)),
            "bookmarks": _count(connection, bookmarks_table, tenant_id),
            "sandbox_checkouts": _count(connection, checkouts, tenant_id),
            "uncertain_checkouts": _count(connection, checkouts, tenant_id, checkouts.c.status == "outcome_unknown"),
            "active_entitlement_records": _count(connection, entitlements, tenant_id, entitlements.c.valid_until > now),
            "reconciliation_attempts": _count(connection, reconciliation_attempts, tenant_id),
        }
        trend = _daily_message_counts(connection, tenant_id, now)
    return {
        "tenant_id": tenant_id,
        "generated_at": now.isoformat(),
        "source": "local_sqlite_persisted_rows",
        "scope": "single_tenant",
        "summary": summary,
        "message_activity_last_7_days": trend,
        "unavailable": {
            "registered_users": "Não existe cadastro de usuários persistido nesta API.",
            "daily_monthly_active_users": "Faltam eventos de sessão/atividade consistentes.",
            "mrr_brl": "Assinaturas recorrentes e liquidação financeira não homologadas.",
            "ai_tokens_and_cost_brl": "Nenhuma telemetria de tokens ou custos por execução foi implementada.",
            "conversion_and_retention": "Eventos de aquisição e coortes ainda não instrumentados.",
        },
        "notice": "Agregados de desenvolvimento: não representam receita, pagamentos recebidos nem métricas de produção.",
    }


@router.get("/overview")
def admin_overview(principal: Principal, response: Response) -> dict[str, object]:
    require_admin(principal)
    settings = get_settings()
    # Never disable/bypass the per-user PostgreSQL RLS to obtain tenant aggregates.
    if settings.environment != "local" or store.engine is None or store.engine.dialect.name != "sqlite":
        raise HTTPException(
            status_code=503,
            detail={"code": "BIBLE_ADMIN_ANALYTICS_RUNTIME_NOT_APPROVED",
                    "message": "Analytics administrativos requerem papel técnico e RLS homologados."},
        )
    try:
        result = snapshot_for_tenant(store.engine, principal.tenant_id)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "BIBLE_ADMIN_ANALYTICS_SCHEMA_NOT_READY",
                    "message": "As tabelas de analytics ainda não estão disponíveis."},
        ) from exc
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Vary"] = "Authorization, X-Tenant-ID, X-User-ID"
    return result
