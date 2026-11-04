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


def test_production_fails_closed_without_oidc_database_and_key(monkeypatch):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "production")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BIBLE_RESTRICTED_DATA_KEY", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="OIDC"):
        get_settings()
    get_settings.cache_clear()
    monkeypatch.setenv("BIBLE_AUTH_MODE", "oidc")
    with pytest.raises(ValueError, match="DATABASE|OIDC"):
        get_settings()
    monkeypatch.setenv("BIBLE_OIDC_ISSUER", "https://issuer.example")
    monkeypatch.setenv("BIBLE_OIDC_AUDIENCE", "bible")
    monkeypatch.setenv("BIBLE_OIDC_INTROSPECTION_ENDPOINT", "https://issuer.example/introspect")
    monkeypatch.setenv("BIBLE_OIDC_CLIENT_ID", "bible-client")
    monkeypatch.setenv("BIBLE_OIDC_CLIENT_SECRET", "test-only")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="DATABASE"):
        get_settings()
