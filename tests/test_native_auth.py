"""Native Bible authentication security contract, SQLite-local proof only.

Staging requires independent PostgreSQL/RLS E2E before AO-01 approval.
"""
from contextlib import contextmanager
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from bible_platform.config import get_settings
from bible_platform.infrastructure.schema import metadata
from bible_platform.main import app
from bible_platform.native_auth import _engine


@pytest.fixture
def client(monkeypatch, tmp_path):
    url = f"sqlite:///{tmp_path / 'identity.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("BIBLE_AUTH_MODE", "native_session")
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_NATIVE_PUBLIC_ORIGIN", "http://testserver")
    monkeypatch.setenv("BIBLE_NATIVE_SESSION_SECRET", "only-test-native-secret-is-long-enough-for-tests-2026")
    monkeypatch.setenv("BIBLE_NATIVE_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv("BIBLE_NATIVE_LOGIN_MAX_ATTEMPTS", "2")
    get_settings.cache_clear()
    _engine.cache_clear()
    with create_engine(url).begin() as conn:
        metadata.create_all(conn)
    with TestClient(app, base_url="http://testserver") as c:
        yield c
    _engine.cache_clear()
    get_settings.cache_clear()


def credentials(email="a@example.org", password="very-safe-test-password"):
    return {"email": email, "password": password}


def register(client, email="a@example.org", password="very-safe-test-password"):
    return client.post("/api/auth/register", json=credentials(email, password),
                       headers={"Origin": "http://testserver"})


def login(client, email="a@example.org", password="very-safe-test-password"):
    return client.post("/api/auth/login", json=credentials(email, password),
                       headers={"Origin": "http://testserver"})


def test_register_login_session_csrf_logout(client):
    assert register(client).status_code == 201
    assert register(client).status_code == 409
    assert client.get("/api/auth/me").status_code == 401
    r = login(client)
    assert r.status_code == 200, r.text
    assert "httponly" in r.headers["set-cookie"].lower()
    session = client.get("/api/auth/session")
    assert session.status_code == 200
    body = session.json()
    assert body["role"] == "member"
    assert body["auth_method"] == "native_session"
    assert body["tenant_id"].startswith("tenant_")
    assert "csrf_token" in body
    assert client.post("/api/auth/logout", headers={"Origin": "http://testserver"}).status_code == 403
    assert client.post("/api/auth/logout",
           headers={"Origin": "https://malicious.example", "X-CSRF-Token": body["csrf_token"]}).status_code == 403
    assert client.get("/api/admin/overview").status_code == 403
    assert client.get("/api/auth/me", headers={"X-Role": "admin"}).status_code == 401
    logout = client.post("/api/auth/logout",
                  headers={"Origin": "http://testserver", "X-CSRF-Token": body["csrf_token"]})
    assert logout.status_code == 200
    assert client.get("/api/auth/session").status_code == 401


def test_registration_origin_reject_and_password_policy(client):
    assert client.post("/api/auth/register", json=credentials()).status_code == 403
    assert client.post("/api/auth/register", json=credentials(),
                       headers={"Origin": "https://bad.example"}).status_code == 403
    assert register(client, password="short").status_code == 422
    assert register(client).status_code == 201


def test_failed_login_and_brute_force_limited_in_db(client):
    assert register(client).status_code == 201
    assert login(client, password="incorrect-password-1").status_code == 401
    assert login(client, password="incorrect-password-2").status_code == 401
    assert login(client).status_code == 429


def test_independent_tenants_no_role_spoof(client):
    assert register(client, "a@example.org").status_code == 201
    assert register(client, "b@example.org").status_code == 201
    assert login(client, "a@example.org").status_code == 200
    a = client.get("/api/auth/me").json()
    assert login(client, "b@example.org").status_code == 200
    b = client.get("/api/auth/me").json()
    assert a["tenant_id"] != b["tenant_id"]
    assert a["role"] == b["role"] == "member"
    forged = client.get("/api/auth/me", headers={"X-Tenant-ID": a["tenant_id"], "X-Role": "admin"})
    assert forged.status_code == 401


def test_registration_flag_blocks_new_users(client, monkeypatch):
    monkeypatch.setenv("BIBLE_NATIVE_REGISTRATION_ENABLED", "false")
    get_settings.cache_clear()
    assert register(client).status_code == 403


def test_reset_disabled_explicitly(client):
    response = client.post("/api/auth/password-reset/request",
                           json={"email": "a@example.org"},
                           headers={"Origin": "http://testserver"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "BIBLE_PASSWORD_RESET_UNAVAILABLE"


def test_reset_revokes_existing_sessions_and_is_single_use(client, monkeypatch):
    from bible_platform import native_auth
    from sqlalchemy import select
    from bible_platform.infrastructure.schema import auth_reset_tokens
    assert register(client).status_code == 201
    assert login(client).status_code == 200
    assert client.get("/api/auth/session").status_code == 200
    monkeypatch.setenv("BIBLE_NATIVE_PASSWORD_RESET_ENABLED", "true")
    monkeypatch.setenv("BIBLE_NATIVE_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("BIBLE_NATIVE_SMTP_USER", "mailer")
    monkeypatch.setenv("BIBLE_NATIVE_SMTP_PASSWORD", "test-only-secret")
    monkeypatch.setenv("BIBLE_NATIVE_SMTP_FROM", "no-reply@example.test")
    get_settings.cache_clear()
    sent = []
    class FakeSMTP:
        def __init__(self, host, port, timeout): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def starttls(self, context): pass
        def login(self, user, password): pass
        def send_message(self, message): sent.append(message)
    monkeypatch.setattr(native_auth.smtplib, "SMTP", FakeSMTP)
    reset = client.post("/api/auth/password-reset/request",
                        json={"email": "a@example.org"},
                        headers={"Origin": "http://testserver"})
    assert reset.status_code == 200
    assert len(sent) == 1
    import re
    token = re.search(r"reset_token=([A-Za-z0-9_\\-]+)", sent[0].get_content()).group(1)
    confirmed = client.post("/api/auth/password-reset/confirm",
                            json={"token": token, "password": "new-strong-password-789"},
                            headers={"Origin": "http://testserver"})
    assert confirmed.status_code == 200, confirmed.text
    assert client.get("/api/auth/session").status_code == 401
    assert client.post("/api/auth/password-reset/confirm",
          json={"token": token, "password": "another-strong-password-789"},
          headers={"Origin": "http://testserver"}).status_code == 400
    assert login(client, password="very-safe-test-password").status_code == 401
    assert login(client, password="new-strong-password-789").status_code == 200
