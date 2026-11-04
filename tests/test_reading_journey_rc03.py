"""API contract regression for the reader -> save -> retrieve -> delete journey."""
from uuid import uuid4

from fastapi.testclient import TestClient


def test_reader_search_bookmark_highlight_roundtrip(monkeypatch):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_TENANTS", "tenant-demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_USERS", "user-demo")
    from bible_platform.config import get_settings
    get_settings.cache_clear()
    from bible_platform.main import app

    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-demo", "X-Role": "member"}

    chapter = client.get("/api/works/work_gen/chapters/1")
    assert chapter.status_code == 200
    first = chapter.json()["units"][0]
    assert first["text_unit_id"] == "tu_work_gen_1_1"
    query = first["text"].split()[0]
    search = client.get("/api/search", params={"q": query})
    assert search.status_code == 200
    assert any(item["text_unit_id"] == first["text_unit_id"] for item in search.json()["results"])

    record = first["text_unit_id"]
    assert client.put(f"/api/bookmarks/{record}", headers=headers).json()["active"] is True
    assert record in client.get("/api/bookmarks", headers=headers).json()["text_unit_ids"]
    assert client.put(f"/api/bookmarks/{record}", headers=headers, params={"active": "false"}).json()["active"] is False
    assert record not in client.get("/api/bookmarks", headers=headers).json()["text_unit_ids"]

    note = f"Estudo {uuid4().hex}"
    created = client.post("/api/highlights", headers=headers, json={
        "text_unit_id": record, "note": note, "color": "gold"
    })
    assert created.status_code == 201
    highlight_id = created.json()["highlight_id"]
    listed = client.get("/api/highlights", headers=headers)
    assert listed.status_code == 200
    assert any(item["highlight_id"] == highlight_id and item["note"] == note for item in listed.json()["items"])
    deleted = client.delete(f"/api/highlights/{highlight_id}", headers=headers)
    assert deleted.status_code == 204
    assert all(item["highlight_id"] != highlight_id for item in client.get("/api/highlights", headers=headers).json()["items"])

    assert client.get("/api/highlights").status_code == 401
