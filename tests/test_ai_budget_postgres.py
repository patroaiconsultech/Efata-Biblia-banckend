"""OPTIONAL true-PostgreSQL concurrency test.

Run ONLY on an explicitly confirmed throwaway database, never the Railway
`railway` production database. The test intentionally persists cost events.
When test PostgreSQL is unavailable this SKIPS; a skip is NOT runtime proof.
"""
import os
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from bible_platform.ai import budget
from bible_platform.domain_models import BiblePrincipal


def test_postgres_atomic_budget_ledger(monkeypatch):
    url = os.getenv("BIBLE_AI_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("No isolated PostgreSQL budget DB supplied")
    if os.getenv("BIBLE_AI_TEST_DATABASE_CONFIRM") != "ISOLATED_AI_TEST_DB":
        pytest.fail("Explicit isolated-DB confirmation is missing")
    parsed = make_url(url)
    if (parsed.get_backend_name() != "postgresql" or
        not (parsed.database or "").startswith("efata_ai_test_")):
        pytest.fail("Forbidden PostgreSQL target: expected efata_ai_test_* database")

    engine = create_engine(url, pool_pre_ping=True, pool_size=12, max_overflow=2)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT NOT rolsuper AND NOT rolbypassrls "
                                     "FROM pg_roles WHERE rolname = current_user")).scalar() is True
            # This test needs a fresh, dedicated DB per run, with migration 010
            # and bible_app grants already in place.
            rows = conn.execute(text(
                "SELECT COUNT(*) FROM public.bible_ai_budget_requests"
            )).scalar_one()
            if rows:
                pytest.fail("AI test database is not fresh; use a NEW disposable test DB")

        monkeypatch.setattr(budget.store, "engine", engine)
        monkeypatch.setenv("BIBLE_AI_GLOBAL_DAILY_BUDGET_CENTS", "1")
        monkeypatch.setenv("BIBLE_AI_PILOT_DAILY_CALLS", "5")
        marker = uuid4().hex

        def one(i):
            principal = BiblePrincipal(
                tenant_id="tenant_" + marker + "_" + str(i),
                user_id="user_" + marker + "_" + str(i),
                role="member", issuer="test", subject="test",
                auth_method="native_session",
            )
            try:
                budget.reserve_budget(principal, "req_" + marker + "_" + str(i))
                return 201
            except HTTPException as exc:
                return exc.status_code

        with ThreadPoolExecutor(max_workers=10) as executor:
            result = list(executor.map(one, range(10)))
        assert result.count(201) == 2
        assert result.count(429) == 8
        with engine.connect() as conn:
            day = conn.execute(text("""
                SELECT reserved_micro_usd, requests
                FROM public.bible_ai_budget_days
            """)).one()
            assert tuple(day) == (10_000, 2)

            # Run the real INSERT against migration 010's actual PostgreSQL
            # table. R3's mistaken "request_id" column fails before here.
            columns = {
                row[0] for row in conn.execute(text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'bible_ai_budget_requests'
                """))
            }
            assert "request_id_digest" in columns
            assert "request_id" not in columns
            attempts = conn.execute(text("""
                SELECT attempt_id, tenant_id, user_id, request_id_digest,
                       status, reserved_micro_usd
                FROM public.bible_ai_budget_requests
                ORDER BY user_id
            """)).all()
            assert len(attempts) == 2
            for attempt in attempts:
                attempt_id, tenant_id, user_id, digest, status, amount = attempt
                suffix = user_id.rsplit("_", 1)[-1]
                assert tenant_id == "tenant_" + marker + "_" + suffix
                raw_request_id = "req_" + marker + "_" + suffix
                assert digest == sha256(raw_request_id.encode("utf-8")).hexdigest()
                assert digest != raw_request_id
                assert status == "reserved"
                assert amount == budget.RESERVE_MICROUSD

        # A duplicate must be rejected before the provider can be charged.
        duplicate = attempts[0]
        principal = BiblePrincipal(
            tenant_id=duplicate[1], user_id=duplicate[2], role="member",
            issuer="test", subject="test", auth_method="native_session",
        )
        duplicate_request_id = "req_" + marker + "_" + duplicate[2].rsplit("_", 1)[-1]
        with pytest.raises(HTTPException) as exc:
            budget.reserve_budget(principal, duplicate_request_id)
        assert exc.value.status_code == 409

        # Metering is persisted into the same real ledger, not just a mock.
        budget.finalize_budget(
            budget.Reservation(duplicate[0]),
            status="completed", input_tokens=100, output_tokens=20,
        )
        with engine.connect() as conn:
            persisted = conn.execute(text("""
                SELECT status, input_tokens, output_tokens,
                       estimated_cost_micro_usd
                FROM public.bible_ai_budget_requests
                WHERE attempt_id = :attempt_id
            """), {"attempt_id": duplicate[0]}).one()
            assert persisted[0] == "completed"
            assert (persisted[1], persisted[2]) == (100, 20)
            assert persisted[3] == budget.cost_estimate_micro_usd(100, 20)
            total = conn.execute(text("""
                SELECT reserved_micro_usd, requests
                FROM public.bible_ai_budget_days
            """)).one()
            assert tuple(total) == (10_000, 2)
    finally:
        engine.dispose()
