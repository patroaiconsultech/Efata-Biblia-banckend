from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from bible_platform.config import get_settings


class EncryptionError(Exception):
    pass


@dataclass(frozen=True)
class EncryptedValue:
    ciphertext: str
    key_version: str


def _fernet() -> Fernet:
    settings = get_settings()
    raw = settings.restricted_data_key.get_secret_value() if settings.restricted_data_key else None
    if raw is None:
        if settings.production:
            raise EncryptionError("BIBLE_RESTRICTED_DATA_KEY_REQUIRED")
        raw = os.getenv("BIBLE_DEV_EPHEMERAL_KEY", "local-only-ephemeral-key")
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_restricted(plaintext: str) -> EncryptedValue:
    if not plaintext:
        raise EncryptionError("BIBLE_RESTRICTED_CONTENT_EMPTY")
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return EncryptedValue(ciphertext=token, key_version="v1")


def decrypt_restricted(value: EncryptedValue) -> str:
    if value.key_version != "v1":
        raise EncryptionError("BIBLE_KEY_VERSION_UNSUPPORTED")
    try:
        return _fernet().decrypt(value.ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise EncryptionError("BIBLE_RESTRICTED_CONTENT_UNDECRYPTABLE") from exc
