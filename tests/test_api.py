import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("BIBLE_ALLOWED_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_TENANTS", "tenant-demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_USERS", "user-demo")
    from bible_platform.config import get_settings
    get_settings.cache_clear()
    from bible_platform.main import app
    return TestClient(app)


def demo_headers():
    return {"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-demo", "X-Role": "member"}


def test_health_and_readiness(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    readiness = client.get("/api/readiness")
    assert readiness.status_code == 200
    assert readiness.json()["production_ready"] is True


def test_catalog_and_reader(client):
    works = client.get("/api/works")
    assert works.status_code == 200
    assert any(item["work_id"] == "work_gen" for item in works.json())
    chapter = client.get("/api/works/work_gen/chapters/1")
    assert chapter.status_code == 200
    assert chapter.json()["units"]
    assert chapter.json()["source"]["source_id"] == "bible-livre"
    assert chapter.json()["rights"]["attribution_required"] is True


def test_protected_routes_fail_closed(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    response = client.get("/api/journal")
    assert response.status_code == 401


def test_restricted_content_is_encrypted_and_scoped(client):
    created = client.post("/api/journal", headers=demo_headers(), json={"content": "Reflexão privada", "kind": "journal"})
    assert created.status_code == 201
    assert created.json()["encrypted"] is True
    entries = client.get("/api/journal", headers=demo_headers())
    assert entries.status_code == 200
    assert entries.json()["items"][0]["content"] == "Reflexão privada"
    denied = client.get("/api/journal", headers={"X-Tenant-ID": "other", "X-User-ID": "user-demo", "X-Role": "member"})
    assert denied.status_code == 401


def test_journey_bookmark_and_highlight(client):
    plans = client.get("/api/reading-plans")
    assert plans.status_code == 200 and plans.json()["plans"]
    today = client.get("/api/journeys/today", headers=demo_headers())
    assert today.status_code == 200 and today.json()["coercive_streaks"] is False
    bookmark = client.put("/api/bookmarks/tu_work_gen_1_1", headers=demo_headers())
    assert bookmark.status_code == 200 and bookmark.json()["active"] is True
    assert "tu_work_gen_1_1" in client.get("/api/bookmarks", headers=demo_headers()).json()["text_unit_ids"]
    highlight = client.post("/api/highlights", headers=demo_headers(), json={"text_unit_id": "tu_work_gen_1_1", "note": "observar contexto", "color": "gold"})
    assert highlight.status_code == 201
    assert client.get("/api/highlights", headers=demo_headers()).json()["items"]


def test_sse_has_terminal_done_and_owner(client):
    response = client.post("/api/conversations/thread-demo/stream", headers=demo_headers(), json={"thread_id": "ignored", "content": "Explique Gênesis 1:1", "mode": "SCRIPTURE_EXPLANATION"})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: meta" in response.text
    assert "event: citation" in response.text
    assert "event: content.final" in response.text
    assert "event: done" in response.text
    assert '"done_observed": true' in response.text
    assert '"agent_id": "bible_guide"' in response.text
    assert '"turn_owner": "bible_guide"' in response.text


def test_message_history_is_tenant_scoped(client):
    history = client.get("/api/conversations/thread-demo/messages", headers=demo_headers())
    assert history.status_code == 200
    assert history.json()["messages"]


def test_rights_gate_blocks_all_text_publication_paths(client, monkeypatch):
    from bible_platform.content.rights import RightsAccessDenied, rights_service

    def deny(*args, **kwargs):
        raise RightsAccessDenied(
            "BIBLE_RIGHTS_REVIEW_REQUIRED",
            "O corpus aguarda revisão de direitos antes da publicação.",
        )

    monkeypatch.setattr(rights_service, "require", deny)

    chapter = client.get("/api/works/work_gen/chapters/1")
    assert chapter.status_code == 503
    assert chapter.json()["detail"]["code"] == "BIBLE_RIGHTS_REVIEW_REQUIRED"

    unit = client.get("/api/text-units/tu_work_gen_1_1")
    assert unit.status_code == 503
    assert unit.json()["detail"]["code"] == "BIBLE_RIGHTS_REVIEW_REQUIRED"

    search = client.get("/api/search", params={"q": "Deus"})
    assert search.status_code == 503
    assert search.json()["detail"]["code"] == "BIBLE_RIGHTS_REVIEW_REQUIRED"

    stream = client.post(
        "/api/conversations/rights-test/stream",
        headers=demo_headers(),
        json={
            "thread_id": "ignored",
            "content": "Explique Gênesis 1:1",
            "mode": "SCRIPTURE_EXPLANATION",
        },
    )
    assert stream.status_code == 200
    assert "BIBLE_RIGHTS_REVIEW_REQUIRED" in stream.text
    assert "event: error" in stream.text
    assert "event: done" in stream.text
    assert "event: citation" not in stream.text
    assert '"block_type": "SCRIPTURE_QUOTE"' not in stream.text


def test_local_browser_preflight_allows_only_configured_origin_and_demo_headers(client):
    allowed = client.options(
        "/api/journal",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-tenant-id,x-user-id,x-role,x-request-id",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"

    denied = client.options(
        "/api/journal",
        headers={
            "Origin": "https://unauthorized.invalid",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-tenant-id",
        },
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers
