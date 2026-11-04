"""Sandbox-only Asaas inbox: no payment, no automatic grant, no live activation."""
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select, func
import pytest

from bible_platform.config import get_settings
from bible_platform.billing.schema import metadata, checkouts, webhook_events, entitlements
from bible_platform.billing import webhook_inbox
from bible_platform.main import app

SECRET = "local-test-only-webhook-secret-000000000000"
HEADERS = {"asaas-access-token": SECRET}


@pytest.fixture(autouse=True)
def isolate_http_rate_limiter():
    # FastAPI's shared in-memory rate limiter must not leak traffic counts
    # from one isolated test to the next. Never disable it in the application.
    from bible_platform.main import _requests
    _requests.clear()
    yield
    _requests.clear()


@pytest.fixture()
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("BIBLE_BILLING_WEBHOOK_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("BIBLE_BILLING_WEBHOOK_TOKEN", SECRET)
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'billing_inbox.sqlite'}")
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(checkouts), [
            {"tenant_id": "tenant-a", "checkout_id": "checkout-abc",
             "user_id": "alice", "plan_code": "caminho",
             "amount_cents": 1990, "idempotency_key": "intent-abc",
             "status": "pending", "provider_ref": "link_abc",
             "provider_url": "https://sandbox.invalid/c/abc",
             "created_at": datetime.now(timezone.utc)},
            {"tenant_id": "tenant-b", "checkout_id": "checkout-def",
             "user_id": "bob", "plan_code": "caminho",
             "amount_cents": 1990, "idempotency_key": "intent-def",
             "status": "pending", "provider_ref": "link_def",
             "provider_url": "https://sandbox.invalid/c/def",
             "created_at": datetime.now(timezone.utc)},
        ])
    monkeypatch.setattr(webhook_inbox, "store", SimpleNamespace(engine=engine))
    yield engine
    engine.dispose()


def payload(event="PAYMENT_RECEIVED", event_id="evt_abc", link="link_abc",
            ref="checkout-abc", value=19.9):
    return {"id": event_id, "event": event,
            "tenant_id": "tenant-b", "user_id": "bob",  # ignored untrusted inputs
            "payment": {"id": "pay_abc", "paymentLink": link,
                        "externalReference": ref, "value": value,
                        "customer": "PRIVATE_SHOULD_NOT_BE_STORED"}}


def test_sandbox_webhook_durable_and_idempotent(sandbox):
    with TestClient(app) as client:
        first = client.post("/api/billing/webhooks/asaas", headers=HEADERS, json=payload())
        assert first.status_code == 200, first.text
        assert first.json() == {"received": True, "duplicate": False, "processed": False}
        repeat = client.post("/api/billing/webhooks/asaas", headers=HEADERS, json=payload())
        assert repeat.status_code == 200
        assert repeat.json()["duplicate"] is True

    with sandbox.connect() as conn:
        stored = conn.execute(select(webhook_events)).mappings().all()
        assert len(stored) == 1
        assert stored[0]["tenant_id"] == "tenant-a"
        assert stored[0]["user_id"] == "alice"
        assert stored[0]["checkout_id"] == "checkout-abc"
        assert stored[0]["processing_status"] == "pending_reconciliation"
        assert conn.execute(select(func.count()).select_from(entitlements)).scalar_one() == 0
    from pathlib import Path
    assert b"PRIVATE_SHOULD_NOT_BE_STORED" not in Path(sandbox.url.database).read_bytes()


def test_requires_token_and_does_not_persist(sandbox):
    with TestClient(app) as client:
        for headers in ({}, {"asaas-access-token": "incorrect"}):
            response = client.post("/api/billing/webhooks/asaas", headers=headers, json=payload())
            assert response.status_code == 401
    with sandbox.connect() as conn:
        assert conn.execute(select(func.count()).select_from(webhook_events)).scalar_one() == 0


@pytest.mark.parametrize("bad", [
    {"link": "unknown"},
    {"link": "link_def", "ref": "checkout-abc"},
    {"value": 2.30},
    {"value": -1},
])
def test_forged_link_and_amount_fail_closed(sandbox, bad):
    with TestClient(app) as client:
        response = client.post("/api/billing/webhooks/asaas",
                               headers=HEADERS, json=payload(**bad))
        assert response.status_code in {422, 503}, response.text
    with sandbox.connect() as conn:
        assert conn.execute(select(func.count()).select_from(webhook_events)).scalar_one() == 0


@pytest.mark.parametrize("missing", ["absent", "null", "empty"])
def test_external_reference_required_before_inbox_persistence(sandbox, missing):
    event = payload(event_id=f"evt_missing_ref_{missing}")
    if missing == "absent":
        del event["payment"]["externalReference"]
    elif missing == "null":
        event["payment"]["externalReference"] = None
    else:
        event["payment"]["externalReference"] = ""

    with TestClient(app) as client:
        result = client.post("/api/billing/webhooks/asaas", headers=HEADERS, json=event)
        assert result.status_code == 422, result.text
        expected = (
            "BIBLE_WEBHOOK_ID_INVALID"
            if missing == "empty"
            else "BIBLE_WEBHOOK_CHECKOUT_REFERENCE_REQUIRED"
        )
        assert result.json()["detail"]["code"] == expected

    with sandbox.connect() as conn:
        assert conn.execute(select(func.count()).select_from(webhook_events)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(entitlements)).scalar_one() == 0


def test_unsupported_event_and_bad_json_fail_closed(sandbox):
    with TestClient(app) as client:
        a = client.post("/api/billing/webhooks/asaas", headers=HEADERS,
                        json=payload(event="USER_UPDATED"))
        assert a.status_code == 422
        b = client.post("/api/billing/webhooks/asaas", headers=HEADERS,
                        content=b"{not json")
        assert b.status_code == 400
        c = client.post("/api/billing/webhooks/asaas", headers=HEADERS,
                        content=b" " * 17000)
        assert c.status_code == 413


def test_disabled_by_default_and_in_staging(sandbox, monkeypatch):
    with TestClient(app) as client:
        monkeypatch.setenv("BIBLE_BILLING_WEBHOOK_SANDBOX_ENABLED", "false")
        assert client.post("/api/billing/webhooks/asaas", headers=HEADERS,
                           json=payload()).status_code == 503
        monkeypatch.setenv("BIBLE_BILLING_WEBHOOK_SANDBOX_ENABLED", "true")
        monkeypatch.setattr(webhook_inbox, "get_settings",
                            lambda: SimpleNamespace(environment="staging"))
        assert client.post("/api/billing/webhooks/asaas", headers=HEADERS,
                           json=payload()).status_code == 503

def test_event_id_collision_is_rejected(sandbox):
    with TestClient(app) as client:
        assert client.post("/api/billing/webhooks/asaas",
                           headers=HEADERS, json=payload()).status_code == 200
        conflicting = client.post("/api/billing/webhooks/asaas",
                                  headers=HEADERS,
                                  json=payload(event_id="evt_abc", event="PAYMENT_REFUNDED"))
        assert conflicting.status_code == 409
        assert conflicting.json()["detail"]["code"] == "BIBLE_WEBHOOK_EVENT_ID_COLLISION"
    with sandbox.connect() as conn:
        assert conn.execute(select(func.count()).select_from(webhook_events)).scalar_one() == 1


def test_payment_event_never_grants_access(sandbox, monkeypatch):
    from bible_platform.billing import routes as billing_routes
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_TENANTS", "tenant-a,tenant-b")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_USERS", "alice,bob")
    get_settings.cache_clear()
    monkeypatch.setattr(billing_routes, "store", SimpleNamespace(engine=sandbox))
    with TestClient(app) as client:
        valid = client.post("/api/billing/webhooks/asaas", headers=HEADERS, json=payload())
        assert valid.status_code == 200
        for tenant, user in (("tenant-a", "alice"), ("tenant-b", "bob")):
            result = client.get("/api/billing/me",
                                headers={"X-Tenant-ID": tenant, "X-User-ID": user})
            assert result.status_code == 200
            assert result.json()["paid_access"] is False
