"""New Bible billing contract: no real charges before settlement/reconciliation gate."""
import json
from datetime import datetime, timedelta, timezone
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from bible_platform.billing.catalog import plans, CatalogError
from bible_platform.billing.asaas_adapter import AsaasClient, ProviderError


def test_catalog_empty_default(monkeypatch):
    monkeypatch.delenv("BIBLE_BILLING_PLANS_JSON", raising=False)
    assert plans() == []


def test_catalog_server_amounts_and_validation(monkeypatch):
    monkeypatch.setenv("BIBLE_BILLING_PLANS_JSON", json.dumps({
        "caminho": {"name": "Caminho", "price_brl": "19.90", "access_days": 30, "features": ["Estudo ampliado"]}
    }))
    row = plans()[0]
    assert row == {"code": "caminho", "name": "Caminho", "price_brl": "19.90", "currency": "BRL",
                   "access_days": 30, "features": ["Estudo ampliado"]}
    for amount in ("-1", "0", "9.999", "NaN", "Infinity"):
        monkeypatch.setenv("BIBLE_BILLING_PLANS_JSON", json.dumps({"caminho": {"name": "Caminho", "price_brl": amount}}))
        with pytest.raises(CatalogError):
            plans()


def test_catalog_malformed_fails_closed(monkeypatch):
    monkeypatch.setenv("BIBLE_BILLING_PLANS_JSON", "{")
    with pytest.raises(CatalogError):
        plans()


def test_asaas_adapter_requires_sandbox():
    with pytest.raises(ProviderError) as exc:
        AsaasClient(api_key="dummy", environment="production")
    assert exc.value.code == "BIBLE_BILLING_LIVE_NOT_APPROVED"


def test_asaas_link_mock_no_real_network():
    seen = []
    def handler(request):
        seen.append(request)
        if request.method == "POST":
            assert request.url.host == "api-sandbox.asaas.com"
            payload = json.loads(request.content)
            assert payload["externalReference"] == "checkout_bible_123"
            assert payload["value"] == 19.9
            return httpx.Response(200, json={"id": "link_1", "url": "https://www.asaas.com/c/abcd"})
        assert request.url.params["externalReference"] == "checkout_bible_123"
        return httpx.Response(200, json={"data": [{"id": "link_1", "url": "https://www.asaas.com/c/abcd", "externalReference": "checkout_bible_123"}]})
    client = AsaasClient(api_key="test-only", transport=httpx.MockTransport(handler))
    plan = {"name": "Caminho", "price_brl": "19.90"}
    checkout = client.create_payment_link(checkout_id="checkout_bible_123", plan=plan, success_url="https://example.test/ok")
    assert checkout["provider_id"] == "link_1"
    assert client.find_by_external_reference("checkout_bible_123") == checkout
    assert len(seen) == 2


def test_asaas_5xx_is_unknown_and_not_retried():
    count = []
    def handler(request):
        count.append(1)
        return httpx.Response(503)
    client = AsaasClient(api_key="test-only", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as exc:
        client.create_payment_link(checkout_id="one", plan={"name": "X", "price_brl": "2.00"}, success_url="https://example.test/ok")
    assert exc.value.outcome_unknown is True
    assert len(count) == 1


def test_billing_routes_readonly_and_fail_closed(monkeypatch):
    monkeypatch.delenv("BIBLE_BILLING_PLANS_JSON", raising=False)
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_TENANTS", "tenant-demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_USERS", "user-demo")
    from bible_platform.config import get_settings
    get_settings.cache_clear()
    from bible_platform.main import app
    with TestClient(app) as client:
        catalog = client.get("/api/billing/plans")
        assert catalog.status_code == 200
        assert catalog.json()["checkout_enabled"] is False
        assert client.get("/api/billing/me").status_code == 401
        headers = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-demo"}
        mine = client.get("/api/billing/me", headers=headers)
        assert mine.status_code == 200 and mine.json()["paid_access"] is False
        checkout = client.post("/api/billing/checkouts", headers=headers,
                               json={"plan_code": "caminho", "idempotency_key": "disabled_checkout_123"})
        assert checkout.status_code == 503
        assert checkout.json()["detail"]["code"] == "BIBLE_BILLING_CHECKOUTS_NOT_ACTIVATED"


def test_billing_entitlements_are_scoped_to_actor_and_expire(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from sqlalchemy import create_engine, insert
    from bible_platform.billing.schema import entitlements, metadata
    from bible_platform.billing import routes
    from bible_platform.config import get_settings
    from bible_platform.main import app
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_TENANTS", "t-a,t-b")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_USERS", "u-a,u-b")
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'actor_billing.sqlite'}")
    metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(insert(entitlements), [
            {"tenant_id": "t-a", "user_id": "u-a", "plan_code": "caminho",
             "valid_until": now + timedelta(days=8),
             "source_checkout_id": "source_paid_verified", "updated_at": now},
            {"tenant_id": "t-b", "user_id": "u-b", "plan_code": "expired",
             "valid_until": now - timedelta(days=2),
             "source_checkout_id": "source_old", "updated_at": now},
        ])
    monkeypatch.setattr(routes, "store", SimpleNamespace(engine=engine))
    with TestClient(app) as client:
        a = client.get("/api/billing/me", headers={"X-Tenant-ID": "t-a", "X-User-ID": "u-a"})
        assert a.status_code == 200 and a.json()["paid_access"] is True
        assert a.json()["plan_code"] == "caminho"
        b = client.get("/api/billing/me", headers={"X-Tenant-ID": "t-b", "X-User-ID": "u-b"})
        assert b.status_code == 200 and b.json()["paid_access"] is False
        other = client.get("/api/billing/me", headers={"X-Tenant-ID": "t-a", "X-User-ID": "u-b"})
        assert other.status_code == 200 and other.json()["paid_access"] is False
        forbidden = client.get("/api/billing/me", headers={"X-Tenant-ID": "t-c", "X-User-ID": "u-a"})
        assert forbidden.status_code == 401
    engine.dispose()


def test_missing_billing_schema_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_TENANTS", "tenant-demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_USERS", "user-demo")
    from bible_platform.config import get_settings
    get_settings.cache_clear()
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from bible_platform.billing import routes
    from bible_platform.main import app
    engine = create_engine(f"sqlite:///{tmp_path / 'not_migrated.sqlite'}")
    monkeypatch.setattr(routes, "store", SimpleNamespace(engine=engine))
    with TestClient(app) as client:
        res = client.get("/api/billing/me", headers={"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-demo"})
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "BIBLE_BILLING_SCHEMA_NOT_READY"
    engine.dispose()
