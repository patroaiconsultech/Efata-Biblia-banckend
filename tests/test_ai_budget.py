"""Financial-budget negative/concurrency unit tests. NEVER use a real API key.

FakePgEngine mimics one atomic row-lock transaction and validates the SQL
contract; a real PostgreSQL integration test remains a separate gate.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock
from types import SimpleNamespace
import os

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from bible_platform.ai import budget, guide
from bible_platform.domain_models import BiblePrincipal


class FakeResult:
    def __init__(self, row=None, count=None, rowcount=1):
        self._row = row
        self._count = count
        self.rowcount = rowcount

    def one(self):
        return self._row

    def one_or_none(self):
        return self._row

    def first(self):
        return self._row

    def scalar_one(self):
        return self._count


class FakePgEngine:
    def __init__(self, *, superuser=False):
        self.lock = RLock()
        self.days = {}
        self.requests = {}
        self.secure = not superuser
        self.seen_sql = []

    @contextmanager
    def begin(self):
        with self.lock:
            yield FakePgConn(self)


class FakePgConn:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, engine):
        self.engine = engine

    def execute(self, statement, params=None):
        sql = " ".join(statement.text.split())
        p = params or {}
        e = self.engine
        e.seen_sql.append(sql)
        if sql.startswith("SELECT rolsuper, rolbypassrls"):
            return FakeResult((not e.secure, not e.secure))
        if sql.startswith("INSERT INTO public.bible_ai_budget_days"):
            e.days.setdefault(p["day"], {"reserved": 0, "requests": 0})
            return FakeResult()
        if "FOR UPDATE" in sql:
            return FakeResult((e.days[p["day"]]["reserved"],))
        if sql.startswith("SELECT 1 FROM public.bible_ai_budget_requests"):
            found = any(
                r["tenant_id"] == p["tenant_id"] and
                r["user_id"] == p["user_id"] and
                r["request_id_digest"] == p["request_id_digest"]
                for r in e.requests.values()
            )
            return FakeResult((1,) if found else None)
        if sql.startswith("SELECT COUNT(*) FROM public.bible_ai_budget_requests"):
            count = sum(
                1 for r in e.requests.values()
                if r["day"] == p["day"] and
                   r["tenant_id"] == p["tenant_id"] and
                   r["user_id"] == p["user_id"]
            )
            return FakeResult(count=count)
        if sql.startswith("INSERT INTO public.bible_ai_budget_requests"):
            e.requests[p["attempt_id"]] = {
                **p, "day": p["day"], "status": "reserved",
                "reserved": p["reserved"],
            }
            return FakeResult()
        if sql.startswith("UPDATE public.bible_ai_budget_days"):
            d = e.days[p["day"]]
            d["reserved"] += p["amount"]
            d["requests"] += 1
            return FakeResult()
        if sql.startswith("UPDATE public.bible_ai_budget_requests"):
            record = e.requests[p["attempt_id"]]
            if record["status"] != "reserved":
                return FakeResult(rowcount=0)
            record.update(status=p["status"],
                          input_tokens=p["input_tokens"],
                          output_tokens=p["output_tokens"],
                          estimate=p["estimated"])
            return FakeResult()
        raise AssertionError(f"Unmocked SQL contract: {sql[:90]}")


def actor(user):
    return BiblePrincipal(
        tenant_id="tenant-" + user, user_id=user, role="member",
        issuer="test", subject=user, auth_method="native_session",
    )


@pytest.fixture(autouse=True)
def isolated_budget(monkeypatch):
    monkeypatch.setenv("BIBLE_AI_GLOBAL_DAILY_BUDGET_CENTS", "1")
    monkeypatch.setenv("BIBLE_AI_PILOT_DAILY_CALLS", "5")
    engine = FakePgEngine()
    monkeypatch.setattr(budget.store, "engine", engine)
    return engine


def test_no_budget_env_fails_closed(monkeypatch):
    monkeypatch.delenv("BIBLE_AI_GLOBAL_DAILY_BUDGET_CENTS")
    with pytest.raises(budget.BudgetUnavailable):
        budget.budget_settings()
    with pytest.raises(budget.BudgetUnavailable):
        budget.reserve_budget(actor("one"), "req_budget_missing")


def test_memory_or_sqlite_cannot_authorize_paid_calls(monkeypatch):
    monkeypatch.setattr(budget.store, "engine", None)
    with pytest.raises(budget.BudgetUnavailable, match="POSTGRES"):
        budget.reserve_budget(actor("one"), "req_no_db")


def test_superuser_role_denied_before_budget_write(isolated_budget):
    isolated_budget.secure = False
    with pytest.raises(budget.BudgetUnavailable):
        budget.reserve_budget(actor("one"), "req_admin")
    assert not isolated_budget.days
    assert not isolated_budget.requests


def test_global_cap_across_two_pilot_users(isolated_budget):
    budget.reserve_budget(actor("one"), "req_a")
    budget.reserve_budget(actor("two"), "req_b")
    with pytest.raises(HTTPException) as exc:
        budget.reserve_budget(actor("three"), "req_c")
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "BIBLE_AI_GLOBAL_BUDGET_EXHAUSTED"
    assert len(isolated_budget.requests) == 2
    assert list(isolated_budget.days.values())[0]["reserved"] == 10_000


def test_idempotency_across_user_request_key(isolated_budget):
    budget.reserve_budget(actor("one"), "req_repeat")
    with pytest.raises(HTTPException) as exc:
        budget.reserve_budget(actor("one"), "req_repeat")
    assert exc.value.status_code == 409
    assert len(isolated_budget.requests) == 1
    assert list(isolated_budget.days.values())[0]["requests"] == 1


def test_concurrent_calls_share_one_budget_row(isolated_budget):
    def reserve(i):
        try:
            budget.reserve_budget(actor("user-" + str(i)), "req_" + str(i))
            return 201
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=12) as pool:
        values = list(pool.map(reserve, range(12)))
    assert values.count(201) == 2
    assert values.count(429) == 10
    assert any("FOR UPDATE" in s for s in isolated_budget.seen_sql)
    assert len(isolated_budget.requests) == 2


def test_per_actor_daily_cap(isolated_budget, monkeypatch):
    monkeypatch.setenv("BIBLE_AI_GLOBAL_DAILY_BUDGET_CENTS", "10")
    for i in range(5):
        budget.reserve_budget(actor("same"), "req_" + str(i))
    with pytest.raises(HTTPException) as exc:
        budget.reserve_budget(actor("same"), "req_six")
    assert exc.value.detail["code"] == "BIBLE_AI_USER_BUDGET_EXHAUSTED"
    assert len(isolated_budget.requests) == 5


def test_finalization_records_only_numeric_usage_and_never_refunds(isolated_budget):
    r = budget.reserve_budget(actor("one"), "req_metered")
    budget.finalize_budget(r, status="completed", input_tokens=1000, output_tokens=500)
    item = isolated_budget.requests[r.attempt_id]
    assert item["status"] == "completed"
    assert item["estimate"] == 350  # $0.000350
    assert list(isolated_budget.days.values())[0]["reserved"] == 5000
    with pytest.raises(budget.BudgetUnavailable):
        budget.finalize_budget(r, status="completed", input_tokens=1, output_tokens=1)
    assert "Pergunta" not in repr(isolated_budget.requests)


def test_unknown_provider_outcome_still_consumes_budget(isolated_budget):
    r = budget.reserve_budget(actor("one"), "req_timeout")
    budget.finalize_budget(r, status="unknown")
    assert isolated_budget.requests[r.attempt_id]["status"] == "unknown"
    assert list(isolated_budget.days.values())[0]["reserved"] == 5000


def test_bad_or_unknown_metering_is_fail_closed(isolated_budget):
    r = budget.reserve_budget(actor("one"), "req_unmetered")
    with pytest.raises(budget.BudgetUnavailable):
        budget.finalize_budget(r, status="completed")
    with pytest.raises(budget.BudgetUnavailable):
        budget.finalize_budget(
            r, status="completed", input_tokens=100_000, output_tokens=900
        )
    assert isolated_budget.requests[r.attempt_id]["status"] == "reserved"


def test_provider_never_called_if_financial_budget_missing(monkeypatch):
    from fastapi import FastAPI
    monkeypatch.setenv("BIBLE_AI_ENABLED", "true")
    monkeypatch.setenv("BIBLE_OPENAI_MODEL", "gpt-6-luna")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "x" * 24)
    monkeypatch.setenv("BIBLE_AI_PILOT_USER_ID", "one")
    monkeypatch.delenv("BIBLE_AI_GLOBAL_DAILY_BUDGET_CENTS")
    called = []
    async def unexpected(*args, **kwargs):
        called.append(True)
        return "Never called"
    monkeypatch.setattr(guide, "generate_ai_text", unexpected)
    monkeypatch.setattr(
        guide, "resolve_intent",
        lambda *args, **kwargs: SimpleNamespace(intent="GENERAL_STUDY"),
    )
    app = FastAPI()
    app.include_router(guide.router)
    app.dependency_overrides[guide.get_principal] = lambda: actor("one")
    res = TestClient(app).post(
        "/api/conversations/main/ai-stream",
        json={"thread_id": "main", "content": "Rotina?", "ai_opt_in": True}
    )
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "BIBLE_AI_BUDGET_UNAVAILABLE"
    assert called == []


def test_cost_estimate_and_config_bounds(monkeypatch):
    assert budget.cost_estimate_micro_usd(1000, 500) == 350
    assert budget.cost_estimate_micro_usd(1, 1) == 1
    with pytest.raises(ValueError):
        budget.cost_estimate_micro_usd(-1, 0)
    monkeypatch.setenv("BIBLE_AI_GLOBAL_DAILY_BUDGET_CENTS", "0")
    with pytest.raises(budget.BudgetUnavailable):
        budget.budget_settings()
    monkeypatch.setenv("BIBLE_AI_GLOBAL_DAILY_BUDGET_CENTS", "1001")
    with pytest.raises(budget.BudgetUnavailable):
        budget.budget_settings()


def test_budget_insert_columns_match_alembic_schema():
    """Regression for R3: SQL may not reference columns absent from migration 010.

    This check runs without PostgreSQL and supplements (does not replace)
    the disposable PostgreSQL integration test.
    """
    import ast
    import inspect
    from pathlib import Path
    import re

    source = inspect.getsource(budget.reserve_budget)
    statement = re.search(
        r"INSERT\s+INTO\s+public\.bible_ai_budget_requests\s*\(([^)]+)\)",
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert statement is not None, "Reservation INSERT not found"
    insert_columns = [item.strip() for item in statement.group(1).split(",")]

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/010_bible_ai_budget.py"
    )
    parsed = ast.parse(migration_path.read_text(encoding="utf-8"))
    create_table = next(
        (
            node for node in ast.walk(parsed)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "bible_ai_budget_requests"
        ),
        None,
    )
    assert create_table is not None, "Budget migration table not found"
    declared_columns = {
        arg.args[0].value
        for arg in create_table.args[1:]
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "Column"
        and arg.args
        and isinstance(arg.args[0], ast.Constant)
    }
    assert len(insert_columns) == len(set(insert_columns))
    assert set(insert_columns) <= declared_columns, (
        f"INSERT references missing columns: {set(insert_columns) - declared_columns}"
    )
    assert "request_id_digest" in insert_columns
    assert "request_id" not in insert_columns
