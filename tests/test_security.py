import pytest

from bible_platform.config import get_settings
from bible_platform.security_crypto import EncryptedValue, EncryptionError, decrypt_restricted, encrypt_restricted


def test_restricted_content_round_trip_and_tamper_detection(monkeypatch):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.delenv("BIBLE_RESTRICTED_DATA_KEY", raising=False)
    get_settings.cache_clear()
    encrypted = encrypt_restricted("conteúdo privado")
    assert encrypted.ciphertext != "conteúdo privado"
    assert decrypt_restricted(encrypted) == "conteúdo privado"
    tampered = encrypted.ciphertext[:-1] + ("A" if encrypted.ciphertext[-1] != "A" else "B")
    with pytest.raises(EncryptionError):
        decrypt_restricted(EncryptedValue(tampered, encrypted.key_version))


def test_production_fails_closed_without_native_database_and_key(monkeypatch):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "production")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BIBLE_RESTRICTED_DATA_KEY", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="NATIVE"):
        get_settings()
    get_settings.cache_clear()
    monkeypatch.setenv("BIBLE_AUTH_MODE", "native_session")
    with pytest.raises(ValueError, match="DATABASE|OIDC"):
        get_settings()
    monkeypatch.setenv("BIBLE_NATIVE_SESSION_SECRET", "test-only-native-session-secret-with-32-or-more-bytes")
    monkeypatch.setenv("BIBLE_NATIVE_PUBLIC_ORIGIN", "https://app.example.org")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="DATABASE"):
        get_settings()


def test_staging_native_refuses_sqlite_and_insecure_origin(monkeypatch):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "staging")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "native_session")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./not-allowed.sqlite")
    monkeypatch.setenv("BIBLE_NATIVE_SESSION_SECRET", "test-only-opaque-native-secret-for-staging")
    monkeypatch.setenv("BIBLE_NATIVE_PUBLIC_ORIGIN", "https://app.example.org")
    monkeypatch.setenv("BIBLE_RESTRICTED_DATA_KEY", "test-only-restricted-key-value-for-staging")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="POSTGRESQL"):
        get_settings()
    get_settings.cache_clear()
