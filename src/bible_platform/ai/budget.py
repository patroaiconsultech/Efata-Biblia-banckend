"""PostgreSQL-backed fail-closed shared AI pilot budget.

No private prompt, provider credential, e-mail or answer is persisted here.
Budget reservations are deliberately NOT refunded after timeouts/errors: a
provider may bill a request even if this application did not receive a result.
All claims serialize on the global UTC-day row, across processes/replicas.

This is an internal control-plane ledger, not an end-user-facing repository.
It must only be accessible through the trusted Bible backend and a non-BYPASSRLS
DB login. Migration 010 and separately reviewed GRANTs are prerequisites.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from hashlib import sha256
import os

from fastapi import HTTPException
from sqlalchemy import text

from bible_platform.domain_models import BiblePrincipal, new_id
from bible_platform.infrastructure.store import store

log = logging.getLogger("bible.ai.budget")
MODEL = "gpt-6-luna"
# Conservative reservation, not an invoice: $0.005/request, held even on
# provider timeout. Actual cost estimates use official STANDARD text prices.
RESERVE_MICROUSD = 5_000
INPUT_MICROUSD_PER_MILLION = 100_000  # $0.10 / 1M input tokens
OUTPUT_MICROUSD_PER_MILLION = 500_000  # $0.50 / 1M output tokens


@dataclass(frozen=True)
class BudgetSettings:
    global_daily_micro_usd: int
    max_user_calls_per_day: int


@dataclass(frozen=True)
class Reservation:
    attempt_id: str


class BudgetUnavailable(RuntimeError):
    """Unavailable authorization/budget infrastructure; never fall back to memory."""


def budget_settings() -> BudgetSettings:
    raw = os.getenv("BIBLE_AI_GLOBAL_DAILY_BUDGET_CENTS", "").strip()
    try:
        cents = int(raw)
    except ValueError as exc:
        raise BudgetUnavailable("BIBLE_AI_BUDGET_NOT_CONFIGURED") from exc
    # The explicit cap must be positive and modest for this pilot.
    if not 1 <= cents <= 1000:
        raise BudgetUnavailable("BIBLE_AI_BUDGET_NOT_CONFIGURED")
    try:
        calls = int(os.getenv("BIBLE_AI_PILOT_DAILY_CALLS", "5"))
    except ValueError as exc:
        raise BudgetUnavailable("BIBLE_AI_BUDGET_NOT_CONFIGURED") from exc
    if not 1 <= calls <= 5:
        raise BudgetUnavailable("BIBLE_AI_BUDGET_NOT_CONFIGURED")
    return BudgetSettings(cents * 10_000, calls)


def _ensure_secure_pg(conn) -> None:
    if conn.dialect.name != "postgresql":
        raise BudgetUnavailable("BIBLE_AI_POSTGRES_REQUIRED")
    privileges = conn.execute(text(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )).one_or_none()
    if not privileges or privileges[0] or privileges[1]:
        raise BudgetUnavailable("BIBLE_AI_UNSAFE_DB_ROLE")


def budget_ready() -> bool:
    """Used only after pilot authorization, before exposing availability."""
    try:
        budget_settings()
        if store.engine is None:
            return False
        with store.engine.connect() as conn:
            _ensure_secure_pg(conn)
            row = conn.execute(text("""
                SELECT
                  to_regclass('public.bible_ai_budget_days') IS NOT NULL,
                  to_regclass('public.bible_ai_budget_requests') IS NOT NULL,
                  (has_table_privilege(current_user, 'public.bible_ai_budget_days', 'SELECT')
                   AND has_table_privilege(current_user, 'public.bible_ai_budget_days', 'INSERT')
                   AND has_table_privilege(current_user, 'public.bible_ai_budget_days', 'UPDATE')),
                  (has_table_privilege(current_user, 'public.bible_ai_budget_requests', 'SELECT')
                   AND has_table_privilege(current_user, 'public.bible_ai_budget_requests', 'INSERT')
                   AND has_table_privilege(current_user, 'public.bible_ai_budget_requests', 'UPDATE'))
            """)).one()
            return all(row)
    except Exception:
        # Readiness is not an authorization bypass and never reveals DB errors.
        return False


def reserve_budget(principal: BiblePrincipal, request_id: str) -> Reservation:
    """Make one atomic cross-replica reservation before a paid provider call.

    A repeated request_id for the same authenticated actor is rejected rather
    than replayed or billed twice. Reservations persist after errors/crashes.
    """
    settings = budget_settings()
    if not principal.tenant_id or not principal.user_id or not request_id or len(request_id) > 120:
        raise BudgetUnavailable("BIBLE_AI_BUDGET_CONTEXT_INVALID")
    if store.engine is None:
        raise BudgetUnavailable("BIBLE_AI_POSTGRES_REQUIRED")
    day = datetime.now(timezone.utc).date()
    attempt_id = new_id("ai")
    # The client chooses request_id; store only an opaque digest, never raw input.
    request_id_digest = sha256(request_id.encode("utf-8")).hexdigest()
    try:
        with store.engine.begin() as conn:
            _ensure_secure_pg(conn)
            conn.execute(text("""
                INSERT INTO public.bible_ai_budget_days
                    (day_utc, reserved_micro_usd, requests, updated_at)
                VALUES (:day, 0, 0, now())
                ON CONFLICT (day_utc) DO NOTHING
            """), {"day": day})
            row = conn.execute(text("""
                SELECT reserved_micro_usd
                FROM public.bible_ai_budget_days
                WHERE day_utc = :day
                FOR UPDATE
            """), {"day": day}).one()
            params = {
                "day": day, "tenant_id": principal.tenant_id,
                "user_id": principal.user_id,
                "request_id_digest": request_id_digest,
            }
            duplicate = conn.execute(text("""
                SELECT 1 FROM public.bible_ai_budget_requests
                WHERE tenant_id = :tenant_id
                  AND user_id = :user_id AND request_id_digest = :request_id_digest
            """), params).first()
            if duplicate:
                raise HTTPException(409, detail={"code": "BIBLE_AI_DUPLICATE_REQUEST"})
            user_count = conn.execute(text("""
                SELECT COUNT(*) FROM public.bible_ai_budget_requests
                WHERE day_utc = :day AND tenant_id = :tenant_id AND user_id = :user_id
            """), params).scalar_one()
            if user_count >= settings.max_user_calls_per_day:
                raise HTTPException(429, detail={"code": "BIBLE_AI_USER_BUDGET_EXHAUSTED"})
            if row[0] + RESERVE_MICROUSD > settings.global_daily_micro_usd:
                raise HTTPException(429, detail={"code": "BIBLE_AI_GLOBAL_BUDGET_EXHAUSTED"})
            conn.execute(text("""
                INSERT INTO public.bible_ai_budget_requests
                    (attempt_id, day_utc, tenant_id, user_id, request_id_digest,
                     model, reserved_micro_usd, status, created_at, updated_at)
                VALUES
                    (:attempt_id, :day, :tenant_id, :user_id, :request_id_digest,
                     :model, :reserved, 'reserved', now(), now())
            """), {
                **params, "attempt_id": attempt_id, "model": MODEL,
                "reserved": RESERVE_MICROUSD,
            })
            conn.execute(text("""
                UPDATE public.bible_ai_budget_days
                SET reserved_micro_usd = reserved_micro_usd + :amount,
                    requests = requests + 1, updated_at = now()
                WHERE day_utc = :day
            """), {"day": day, "amount": RESERVE_MICROUSD})
    except HTTPException as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else "denied"
        log.info("bible_ai_budget_denied code=%s", code)
        raise
    except Exception as exc:
        # Do not print the exception: drivers may contain secrets/DSNs.
        log.warning("bible_ai_budget_reservation_failed error_class=%s", type(exc).__name__)
        raise BudgetUnavailable("BIBLE_AI_BUDGET_UNAVAILABLE") from exc
    return Reservation(attempt_id)


def cost_estimate_micro_usd(input_tokens: int, output_tokens: int) -> int:
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        raise ValueError("invalid usage")
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("invalid usage")
    numerator = (
        input_tokens * INPUT_MICROUSD_PER_MILLION
        + output_tokens * OUTPUT_MICROUSD_PER_MILLION
    )
    return (numerator + 999_999) // 1_000_000


def finalize_budget(reservation: Reservation, *, status: str,
                    input_tokens: int | None = None,
                    output_tokens: int | None = None) -> None:
    """Record usage or an unknown outcome. Never refund the reservation.

    A provider call may have succeeded even when its response was lost.
    """
    if status not in ("completed", "unknown"):
        raise ValueError("invalid status")
    estimated = None
    if input_tokens is not None and output_tokens is not None:
        estimated = cost_estimate_micro_usd(input_tokens, output_tokens)
    # Missing usage is explicitly unknown, not a zero-cost success.
    if status == "completed" and estimated is None:
        # A successful provider response without usage is not a verified
        # metering event. The caller must return an explicit SSE error.
        raise BudgetUnavailable("BIBLE_AI_USAGE_NOT_METERED")
    if estimated is not None and estimated > RESERVE_MICROUSD:
        raise BudgetUnavailable("BIBLE_AI_RESERVATION_EXCEEDED")
    if store.engine is None:
        raise BudgetUnavailable("BIBLE_AI_POSTGRES_REQUIRED")
    try:
        with store.engine.begin() as conn:
            _ensure_secure_pg(conn)
            result = conn.execute(text("""
                UPDATE public.bible_ai_budget_requests
                SET status = :status,
                    input_tokens = :input_tokens,
                    output_tokens = :output_tokens,
                    estimated_cost_micro_usd = :estimated,
                    updated_at = now()
                WHERE attempt_id = :attempt_id AND status = 'reserved'
            """), {
                "attempt_id": reservation.attempt_id,
                "status": status,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated": estimated,
            })
            if result.rowcount != 1:
                raise BudgetUnavailable("BIBLE_AI_BUDGET_FINALIZE_CONFLICT")
    except Exception as exc:
        log.warning("bible_ai_budget_finalize_failed error_class=%s", type(exc).__name__)
        raise BudgetUnavailable("BIBLE_AI_BUDGET_UNAVAILABLE") from exc
    # Structured metrics ONLY: no prompt, identity, cookie, key or answer.
    log.info(
        "bible_ai_request_result status=%s input_tokens=%s output_tokens=%s "
        "estimated_cost_micro_usd=%s reserved_micro_usd=%d",
        status, input_tokens, output_tokens, estimated, RESERVE_MICROUSD,
    )
