from __future__ import annotations

import pytest

from bible_platform.config import get_settings
from bible_platform.infrastructure.store import Store


@pytest.fixture
def persistent_store(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'restarted.db'}")
    monkeypatch.setenv("BIBLE_AUTO_CREATE_SCHEMA", "true")
    get_settings.cache_clear()
    yield Store
    get_settings.cache_clear()


def test_personal_delete_after_restart_is_not_false_not_found(persistent_store):
    first = persistent_store()
    first.create_personal("tenant-a", "user-a", "entry-1", "journal", "privado")
    second = persistent_store()
    assert second.list_personal("tenant-a", "user-a")[0]["content"] == "privado"
    with pytest.raises(KeyError):
        second.delete_personal("tenant-b", "user-a", "entry-1")
    with pytest.raises(KeyError):
        second.delete_personal("tenant-a", "user-b", "entry-1")
    second.delete_personal("tenant-a", "user-a", "entry-1")
    assert persistent_store().list_personal("tenant-a", "user-a") == []
    with pytest.raises(KeyError):
        persistent_store().delete_personal("tenant-a", "user-a", "entry-1")


def test_highlight_delete_after_restart_is_not_false_not_found(persistent_store):
    first = persistent_store()
    first.add_highlight("tenant-a", "user-a", "hl-1", "text-1", "anotação", "gold")
    second = persistent_store()
    assert len(second.list_highlights("tenant-a", "user-a")) == 1
    with pytest.raises(KeyError):
        second.delete_highlight("tenant-a", "user-b", "hl-1")
    with pytest.raises(KeyError):
        second.delete_highlight("tenant-b", "user-a", "hl-1")
    second.delete_highlight("tenant-a", "user-a", "hl-1")
    assert persistent_store().list_highlights("tenant-a", "user-a") == []
    with pytest.raises(KeyError):
        persistent_store().delete_highlight("tenant-a", "user-a", "hl-1")
