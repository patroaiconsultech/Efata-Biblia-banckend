from bible_platform.config import get_settings
from bible_platform.infrastructure.store import Store


def test_sqlite_store_persists_encrypted_personal_data(monkeypatch, tmp_path):
    db_file = tmp_path / "bible.db"
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("BIBLE_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_TENANTS", "tenant-demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_USERS", "user-demo")
    get_settings.cache_clear()
    local_store = Store()
    entry = local_store.create_personal("tenant-demo", "user-demo", "entry_sql", "journal", "texto protegido")
    assert entry.ciphertext != "texto protegido"
    assert local_store.list_personal("tenant-demo", "user-demo")[0]["content"] == "texto protegido"
    get_settings.cache_clear()



def test_highlight_notes_are_encrypted_and_owner_scoped_after_restart(monkeypatch, tmp_path):
    from sqlalchemy import text
    db_file = tmp_path / "highlights.db"
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("BIBLE_AUTO_CREATE_SCHEMA", "true")
    get_settings.cache_clear()

    first = Store()
    response = first.add_highlight("tenant-a", "owner-a", "highlight-1", "tu_work_gen_1_1", "nota confidencial", "gold")
    assert response["note"] == "nota confidencial"

    with first.engine.connect() as conn:
        stored_note = conn.execute(text("SELECT note FROM bible_highlights WHERE highlight_id = 'highlight-1'")).scalar_one()
    assert stored_note.startswith("enc:v1:")
    assert "nota confidencial" not in stored_note

    restarted = Store()
    assert restarted.list_highlights("tenant-a", "owner-a")[0]["note"] == "nota confidencial"
    assert restarted.list_highlights("tenant-a", "other-user") == []
    assert restarted.list_highlights("another-tenant", "owner-a") == []

    restarted.delete_highlight("tenant-a", "owner-a", "highlight-1")
    assert restarted.list_highlights("tenant-a", "owner-a") == []
    get_settings.cache_clear()
