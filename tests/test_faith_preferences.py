"""No religious profiling without active, explicit opt-in."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from bible_platform.domain_models import BiblePrincipal
from bible_platform.faith.profile import get_consenting_preferences, optional_guide_context
from bible_platform.infrastructure.schema import metadata
from bible_platform.infrastructure.store import store


@pytest.fixture
def setup_profile_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_TENANTS", "tenant-demo,tenant-other")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_USERS", "user-demo,user-other")
    from bible_platform.config import get_settings
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path/'profiles.db'}")
    metadata.create_all(engine)
    monkeypatch.setattr(store, "engine", engine)
    from bible_platform.main import app, _requests
    _requests.clear()
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()
    get_settings.cache_clear()


def headers(tenant="tenant-demo", user="user-demo"):
    return {"X-Tenant-ID": tenant, "X-User-ID": user, "X-Role": "member"}


def payload(tradition="catholic", style="my_tradition"):
    return {
        "tradition": tradition,
        "study_style": style,
        "consent": True,
        "consent_version": "faith-preferences-v1",
    }


def test_opt_in_is_explicit_and_absence_does_not_block_reading(setup_profile_db):
    client, _ = setup_profile_db
    assert client.get("/api/faith-preferences/me", headers=headers()).json()["preference"] is None
    assert client.get("/api/works").status_code == 200
    for invalid in (
        {"tradition": "catholic", "study_style": "my_tradition"},
        {**payload(), "consent": False},
        {**payload(), "consent_version": "outdated"},
        {**payload(), "secret": "unexpected"},
        {**payload(), "tradition": "not-in-allowlist"},
    ):
        assert client.put("/api/faith-preferences/me", json=invalid, headers=headers()).status_code == 422
    assert client.get("/api/faith-preferences/me", headers=headers()).json()["configured"] is False


def test_roundtrip_is_encrypted_and_tenant_user_scoped(setup_profile_db):
    client, engine = setup_profile_db
    saved = client.put("/api/faith-preferences/me", json=payload(), headers=headers())
    assert saved.status_code == 200, saved.text
    assert saved.json()["preference"] == {"tradition": "catholic", "study_style": "my_tradition"}
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT tenant_id, user_id, ciphertext, key_version FROM bible_faith_preferences")).mappings().all()
    assert len(rows) == 1 and rows[0]["tenant_id"] == "tenant-demo"
    assert rows[0]["user_id"] == "user-demo" and rows[0]["key_version"] == "v1"
    assert "catholic" not in rows[0]["ciphertext"]
    assert client.get("/api/faith-preferences/me", headers=headers("tenant-other")).json()["preference"] is None
    assert client.get("/api/faith-preferences/me", headers=headers(user="user-other")).json()["preference"] is None
    assert client.put("/api/faith-preferences/me", json=payload("orthodox", "compare"), headers=headers("tenant-other")).status_code == 200
    assert client.get("/api/faith-preferences/me", headers=headers()).json()["preference"]["tradition"] == "catholic"
    assert client.delete("/api/faith-preferences/me", headers=headers()).status_code == 200
    assert client.delete("/api/faith-preferences/me", headers=headers()).status_code == 200
    assert client.get("/api/faith-preferences/me", headers=headers()).json()["configured"] is False
    assert client.get("/api/faith-preferences/me", headers=headers("tenant-other")).json()["preference"]["tradition"] == "orthodox"


def test_update_overwrites_and_delete_removes_ciphertext(setup_profile_db):
    client, engine = setup_profile_db
    client.put("/api/faith-preferences/me", json=payload(), headers=headers())
    response = client.put("/api/faith-preferences/me", json=payload("jewish", "ecumenical"), headers=headers())
    assert response.status_code == 200
    assert response.json()["preference"]["tradition"] == "jewish"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM bible_faith_preferences")).scalar() == 1
    client.delete("/api/faith-preferences/me", headers=headers())
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM bible_faith_preferences")).scalar() == 0


def test_context_does_not_fake_denominational_expertise():
    assert optional_guide_context(None) is None
    assert optional_guide_context({"tradition": "catholic", "study_style": "ecumenical"}) is None
    assert "não possui comentários" in optional_guide_context({"tradition": "catholic", "study_style": "my_tradition"})
    assert "comparar perspectivas" in optional_guide_context({"tradition": "orthodox", "study_style": "compare"})


def test_demo_without_allowed_identity_does_not_read_preference(setup_profile_db):
    client, _ = setup_profile_db
    client.put("/api/faith-preferences/me", json=payload(), headers=headers())
    assert client.get("/api/faith-preferences/me", headers={"X-Tenant-ID": "other", "X-User-ID": "user-demo"}).status_code == 401
    assert client.get("/api/faith-preferences/me").status_code == 401


def test_guide_uses_opted_in_context_without_changing_scripture(setup_profile_db):
    client, _ = setup_profile_db
    assert client.put("/api/faith-preferences/me", json=payload(), headers=headers()).status_code == 200

    response = client.post("/api/conversations/faith-profile-stream/stream", headers=headers(), json={
        "thread_id": "faith-profile-stream",
        "content": "Como posso organizar um estudo bíblico?",
        "mode": "SCRIPTURE_EXPLANATION",
    })
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    blocks = [event["payload"] for event in events if event["event_type"] == "content.delta"]
    assert any(b["block_type"] == "INTERPRETATION" for b in blocks)
    assert any("tradição católica" in b.get("delta", "") for b in blocks if b["block_type"] == "INTERPRETATION")
    assert events[-1]["event_type"] == "done"

    # Reading-only requests have explicit priority over optional framing.
    from bible_platform.behavior.intent import resolve_intent
    assert resolve_intent("Leia Gênesis 1:1, apenas o texto", "SCRIPTURE_EXPLANATION").intent == "SCRIPTURE_READING"
    # The source-rights gate is independent and still blocks unlicensed quotations.

    client.delete("/api/faith-preferences/me", headers=headers())
    response3 = client.post("/api/conversations/faith-profile-no-consent/stream", headers=headers(), json={
        "thread_id": "faith-profile-no-consent",
        "content": "Como posso organizar um estudo bíblico?",
    })
    events3 = [json.loads(line[6:]) for line in response3.text.splitlines() if line.startswith("data: ")]
    assert all("tradição católica" not in (e.get("payload", {}).get("delta", "") or "") for e in events3)
