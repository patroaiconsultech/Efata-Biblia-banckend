"""Sandbox checkout: no real provider calls; no premium entitlement grants."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from bible_platform.billing import sandbox_checkout
from bible_platform.billing.schema import checkouts, entitlements
from bible_platform.infrastructure.schema import metadata
from bible_platform.config import get_settings
from bible_platform.main import app
from bible_platform.billing.asaas_adapter import ProviderError


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_TENANTS", "ta,tb")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_USERS", "ua,ub")
    monkeypatch.setenv("BIBLE_BILLING_SANDBOX_CHECKOUT_ENABLED", "true")
    monkeypatch.setenv("BIBLE_BILLING_ASAAS_SANDBOX_KEY", "test-only-key-not-real-secret")
    monkeypatch.setenv("BIBLE_BILLING_SANDBOX_SUCCESS_URL", "https://example.test/success")
    monkeypatch.setenv("BIBLE_BILLING_PLANS_JSON", json.dumps({
        "essencial": {"name": "Essencial", "price_brl": "29.90"},
        "jornada": {"name": "Jornada", "price_brl": "59.90"},
    }))
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'sandbox.sqlite'}")
    metadata.create_all(engine)
    monkeypatch.setattr(sandbox_checkout, "store", SimpleNamespace(engine=engine))
    yield engine
    engine.dispose()
    get_settings.cache_clear()


def headers(tenant="ta", user="ua"):
    return {"X-Tenant-ID": tenant, "X-User-ID": user}


def test_checkout_disabled_by_default(monkeypatch, sandbox):
    monkeypatch.setenv("BIBLE_BILLING_SANDBOX_CHECKOUT_ENABLED", "false")
    with TestClient(app) as client:
        p = client.post("/api/billing/checkouts", headers=headers(),
                        json={"plan_code": "essencial", "idempotency_key": "valid_key_12345"})
        assert p.status_code == 503
    with sandbox.connect() as db:
        assert db.execute(select(checkouts)).all() == []


def test_checkout_rejects_untrusted_amount_and_missing_key(sandbox):
    with TestClient(app) as client:
        r = client.post("/api/billing/checkouts", headers=headers(), json={
            "plan_code": "essencial", "idempotency_key": "valid_key_12345", "amount_cents": 1,
        })
        assert r.status_code == 422
        r = client.post("/api/billing/checkouts", headers=headers(), json={"plan_code": "essencial"})
        assert r.status_code == 422


def test_checkout_idempotency_tenant_scope_and_no_grant(monkeypatch, sandbox):
    calls = []
    class Stub:
        def create_payment_link(self, *, checkout_id, plan, success_url):
            calls.append((checkout_id, plan["price_brl"], success_url))
            return {"provider_id": "link_"+str(len(calls)),
                    "payment_url": "https://www.asaas.com/c/test" + str(len(calls))}
    monkeypatch.setattr(sandbox_checkout, "_provider", lambda: Stub())
    key = "unique_checkout_key_123"
    with TestClient(app) as client:
        r = client.post("/api/billing/checkouts", headers=headers(), json={
            "plan_code": "essencial", "idempotency_key": key,
        })
        assert r.status_code == 200, r.text
        assert r.json()["replayed"] is False
        repeat = client.post("/api/billing/checkouts", headers=headers(), json={
            "plan_code": "essencial", "idempotency_key": key,
        })
        assert repeat.status_code == 200
        assert repeat.json()["replayed"] is True
        assert repeat.json()["checkout_id"] == r.json()["checkout_id"]
        assert len(calls) == 1
        conflict = client.post("/api/billing/checkouts", headers=headers(), json={
            "plan_code": "jornada", "idempotency_key": key,
        })
        assert conflict.status_code == 409
        user_b = client.post("/api/billing/checkouts", headers=headers("ta", "ub"), json={
            "plan_code": "essencial", "idempotency_key": key,
        })
        assert user_b.status_code == 200
        tenant_b = client.post("/api/billing/checkouts", headers=headers("tb", "ua"), json={
            "plan_code": "essencial", "idempotency_key": key,
        })
        assert tenant_b.status_code == 200
        assert len(calls) == 3
    with sandbox.connect() as db:
        rows = db.execute(select(checkouts)).mappings().all()
        assert len(rows) == 3
        assert all(x["amount_cents"] == 2990 for x in rows)
        assert db.execute(select(entitlements)).all() == []


def test_provider_timeout_fails_closed_and_never_retries(monkeypatch, sandbox):
    calls = []
    class Bad:
        def create_payment_link(self, **kw):
            calls.append(kw)
            raise ProviderError("BIBLE_BILLING_PROVIDER_OUTCOME_UNKNOWN")
    monkeypatch.setattr(sandbox_checkout, "_provider", lambda: Bad())
    body = {"plan_code": "essencial", "idempotency_key": "timeout_checkout_123"}
    with TestClient(app) as client:
        first = client.post("/api/billing/checkouts", headers=headers(), json=body)
        second = client.post("/api/billing/checkouts", headers=headers(), json=body)
        assert first.status_code == 503
        assert second.status_code == 409
        assert len(calls) == 1
    with sandbox.connect() as db:
        assert db.execute(select(checkouts.c.status)).scalar() == "outcome_unknown"
        assert db.execute(select(entitlements)).all() == []


def test_reject_unsafe_provider_payment_url(monkeypatch, sandbox):
    class Attack:
        def create_payment_link(self, **kw):
            return {"provider_id": "link_1", "payment_url": "https://fake-asaas.com/c/pay"}
    monkeypatch.setattr(sandbox_checkout, "_provider", lambda: Attack())
    with TestClient(app) as client:
        r = client.post("/api/billing/checkouts", headers=headers(), json={
            "plan_code": "essencial", "idempotency_key": "badredirect_key_123",
        })
        assert r.status_code == 503
    with sandbox.connect() as db:
        assert db.execute(select(checkouts.c.status)).scalar() == "outcome_unknown"


def test_checkout_unknown_plan_and_no_database_insert(sandbox):
    with TestClient(app) as client:
        r = client.post("/api/billing/checkouts", headers=headers(), json={
            "plan_code": "vip_inventado", "idempotency_key": "invalidplan_key_123",
        })
        assert r.status_code == 422
    with sandbox.connect() as db:
        assert db.execute(select(checkouts)).all() == []


def test_disabled_on_staging_even_if_flag_set(monkeypatch, sandbox):
    monkeypatch.setattr(sandbox_checkout, "get_settings", lambda: SimpleNamespace(environment="staging"))
    with TestClient(app) as client:
        r = client.post("/api/billing/checkouts", headers=headers(), json={
            "plan_code": "essencial", "idempotency_key": "attempt_staging_123",
        })
        assert r.status_code == 503
    with sandbox.connect() as db:
        assert db.execute(select(checkouts)).all() == []
