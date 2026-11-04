"""RC 0.14 tenant-scoped, privacy-preserving admin snapshot tests."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert

from bible_platform.config import get_settings
from bible_platform.main import app, _requests
from bible_platform.admin import overview
from bible_platform.infrastructure.schema import metadata, threads_table, messages_table, progress_table, bookmarks_table
from bible_platform.billing.schema import checkouts, entitlements, reconciliation_attempts


@pytest.fixture
def client_and_engine(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_TENANTS", "tenant-a,tenant-b")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_USERS", "admin-a,admin-b,member-a")
    monkeypatch.setenv("BIBLE_DEMO_ADMIN_USERS", "admin-a,admin-b")
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'analytics.sqlite'}")
    metadata.create_all(engine)
    monkeypatch.setattr(overview, "store", SimpleNamespace(engine=engine))
    _requests.clear()
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()
    get_settings.cache_clear()


def headers(tenant="tenant-a", user="admin-a", role="admin"):
    return {"X-Tenant-ID": tenant, "X-User-ID": user, "X-Role": role}


def insert_rows(engine):
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        for tenant, user, thread in [
            ("tenant-a", "member-a", "a1"), ("tenant-a", "admin-a", "a2"),
            ("tenant-b", "admin-b", "b1"),
        ]:
            conn.execute(insert(threads_table).values(tenant_id=tenant, thread_id=thread, user_id=user,
                                                     title="PRIVATE TITLE", created_at=now))
            conn.execute(insert(messages_table).values(
                tenant_id=tenant, message_id=f"msg_{thread}", thread_id=thread, user_id=user,
                role="user", blocks_json='{"private":"NEVER EXPOSE"}', created_at=now))
        conn.execute(insert(progress_table).values(tenant_id="tenant-a", user_id="member-a",
                text_unit_id="x", completed=True, updated_at=now))
        conn.execute(insert(bookmarks_table).values(tenant_id="tenant-a", user_id="member-a",
                text_unit_id="x", created_at=now))
        conn.execute(insert(checkouts).values(tenant_id="tenant-a", checkout_id="c1", user_id="member-a",
                plan_code="essencial", amount_cents=2990, idempotency_key="mykey", status="outcome_unknown",
                provider_ref=None, provider_url=None, created_at=now))
        conn.execute(insert(entitlements).values(tenant_id="tenant-a", user_id="member-a",
                plan_code="essencial", valid_until=now+timedelta(days=2), source_checkout_id="c1", updated_at=now))
        conn.execute(insert(reconciliation_attempts).values(
            attempt_id="rec1", tenant_id="tenant-a", user_id="member-a", checkout_id="c1",
            payment_id="p1", event_id="ev1", result="payment_pending",
            provider_status="PENDING", error_code=None, created_at=now))


def test_admin_required_and_user_cannot_spoof_admin(client_and_engine):
    client, _ = client_and_engine
    assert client.get("/api/admin/overview").status_code == 401
    assert client.get("/api/admin/overview", headers=headers(role="member")).status_code == 403
    assert client.get("/api/admin/overview", headers=headers(user="member-a", role="admin")).status_code == 403
    assert client.get("/api/admin/overview", headers=headers(user="random", role="admin")).status_code == 401


def test_tenant_aggregate_no_private_data_and_uninstrumented_metrics(client_and_engine):
    client, engine = client_and_engine
    insert_rows(engine)
    one = client.get("/api/admin/overview", headers=headers()).json()
    two = client.get("/api/admin/overview", headers=headers(tenant="tenant-b", user="admin-b")).json()
    assert one["scope"] == "single_tenant" and one["tenant_id"] == "tenant-a"
    assert one["summary"]["users_with_persisted_activity"] == 2
    assert one["summary"]["conversations"] == 2
    assert one["summary"]["messages"] == 2
    assert one["summary"]["completed_readings"] == 1
    assert one["summary"]["bookmarks"] == 1
    assert one["summary"]["sandbox_checkouts"] == 1
    assert one["summary"]["uncertain_checkouts"] == 1
    assert one["summary"]["active_entitlement_records"] == 1
    assert one["summary"]["reconciliation_attempts"] == 1
    assert two["summary"]["users_with_persisted_activity"] == 1
    assert two["summary"]["conversations"] == 1
    assert two["summary"]["sandbox_checkouts"] == 0
    assert len(one["message_activity_last_7_days"]) == 7
    assert sum(x["messages"] for x in one["message_activity_last_7_days"]) == 2
    assert "mrr_brl" in one["unavailable"]
    assert "ai_tokens_and_cost_brl" in one["unavailable"]
    assert "NEVER EXPOSE" not in str(one)
    assert "PRIVATE TITLE" not in str(one)
    assert "member-a" not in str(one)


def test_admin_headers_no_cache(client_and_engine):
    client, _ = client_and_engine
    response = client.get("/api/admin/overview", headers=headers())
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["x-robots-tag"] == "noindex, nofollow"


def test_fails_closed_without_local_sqlite(client_and_engine, monkeypatch):
    client, _ = client_and_engine
    monkeypatch.setattr(overview, "store", SimpleNamespace(engine=None))
    res = client.get("/api/admin/overview", headers=headers())
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "BIBLE_ADMIN_ANALYTICS_RUNTIME_NOT_APPROVED"


def test_fails_closed_in_other_nonproduction_environment(client_and_engine, monkeypatch):
    client, _ = client_and_engine
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "qa")
    get_settings.cache_clear()
    res = client.get("/api/admin/overview", headers=headers())
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "BIBLE_ADMIN_ANALYTICS_RUNTIME_NOT_APPROVED"
