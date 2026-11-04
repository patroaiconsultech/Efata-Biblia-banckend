"""Explicitly opted-in preferences. Not a religious identity inference engine.

Only the authenticated owner can read/write/delete. No billing or entitlement use.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, insert, select, update

from bible_platform.api.dependencies import get_principal
from bible_platform.domain_models import BiblePrincipal
from bible_platform.infrastructure.schema import faith_preferences_table
from bible_platform.infrastructure.store import _set_rls_context, store
from bible_platform.security_crypto import EncryptedValue, decrypt_restricted, encrypt_restricted

Tradition = Literal[
    "catholic", "evangelical", "protestant", "orthodox", "other_christian",
    "jewish", "other", "none",
]
StudyStyle = Literal["ecumenical", "my_tradition", "compare"]
CONSENT_VERSION = "faith-preferences-v1"
router = APIRouter(prefix="/api/faith-preferences", tags=["faith-preferences"])


class FaithPreferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tradition: Tradition
    study_style: StudyStyle = "ecumenical"
    consent: Literal[True]
    consent_version: Literal["faith-preferences-v1"]


def _read(principal: BiblePrincipal) -> dict | None:
    if store.engine is not None:
        with store.engine.begin() as conn:
            _set_rls_context(conn, principal.tenant_id, principal.user_id)
            row = conn.execute(select(faith_preferences_table).where(
                faith_preferences_table.c.tenant_id == principal.tenant_id,
                faith_preferences_table.c.user_id == principal.user_id,
            )).mappings().first()
            payload = dict(row) if row else None
    else:
        with store._lock:
            payload = store._faith_preferences.get((principal.tenant_id, principal.user_id))
    if not payload:
        return None
    decrypted = decrypt_restricted(EncryptedValue(
        ciphertext=payload["ciphertext"], key_version=payload["key_version"]
    ))
    data = json.loads(decrypted)
    # Fail closed if persisted content was not created using current allowlists.
    validated = FaithPreferenceInput.model_validate({**data, "consent": True, "consent_version": CONSENT_VERSION})
    return {"tradition": validated.tradition, "study_style": validated.study_style}


def get_consenting_preferences(principal: BiblePrincipal) -> dict | None:
    return _read(principal)


@router.get("/me")
def my_preference(principal: BiblePrincipal = Depends(get_principal)) -> dict:
    data = _read(principal)
    return {"configured": data is not None, "consent_version": CONSENT_VERSION, "preference": data}


@router.put("/me")
def save_preference(payload: FaithPreferenceInput, principal: BiblePrincipal = Depends(get_principal)) -> dict:
    data = {"tradition": payload.tradition, "study_style": payload.study_style}
    encrypted = encrypt_restricted(json.dumps(data, ensure_ascii=False))
    now = datetime.now(timezone.utc)
    values = {
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "ciphertext": encrypted.ciphertext,
        "key_version": encrypted.key_version,
        "consent_version": CONSENT_VERSION,
        "updated_at": now,
    }
    if store.engine is not None:
        with store.engine.begin() as conn:
            _set_rls_context(conn, principal.tenant_id, principal.user_id)
            result = conn.execute(update(faith_preferences_table).where(
                faith_preferences_table.c.tenant_id == principal.tenant_id,
                faith_preferences_table.c.user_id == principal.user_id,
            ).values(**{key: value for key, value in values.items() if key not in ("tenant_id", "user_id")}))
            if result.rowcount == 0:
                conn.execute(insert(faith_preferences_table).values(**values))
    else:
        with store._lock:
            store._faith_preferences[(principal.tenant_id, principal.user_id)] = values
    return {"configured": True, "consent_version": CONSENT_VERSION, "preference": data}


@router.delete("/me")
def delete_preference(principal: BiblePrincipal = Depends(get_principal)) -> dict:
    if store.engine is not None:
        with store.engine.begin() as conn:
            _set_rls_context(conn, principal.tenant_id, principal.user_id)
            conn.execute(delete(faith_preferences_table).where(
                faith_preferences_table.c.tenant_id == principal.tenant_id,
                faith_preferences_table.c.user_id == principal.user_id,
            ))
    else:
        with store._lock:
            store._faith_preferences.pop((principal.tenant_id, principal.user_id), None)
    return {"configured": False, "consent_version": CONSENT_VERSION, "preference": None}


TRADITION_NAMES = {
    "catholic": "católica",
    "evangelical": "evangélica",
    "protestant": "protestante",
    "orthodox": "ortodoxa",
    "other_christian": "outra tradição cristã",
    "jewish": "judaica",
    "other": "outra tradição",
    "none": "sem vínculo religioso",
}


def optional_guide_context(preference: dict | None) -> str | None:
    """A disclosure, never a doctrine claim or a replacement of scripture."""
    if not preference or preference["study_style"] == "ecumenical":
        return None
    if preference["study_style"] == "compare":
        return (
            "Você optou por comparar perspectivas religiosas. Este catálogo ainda não dispõe "
            "de fontes denominacionais completas e revisadas para fazer comparações aprofundadas. "
            "A passagem bíblica exibida mantém sua tradução e proveniência originais."
        )
    return (
        f"Você informou afinidade com a tradição {TRADITION_NAMES[preference['tradition']]}. "
        "Acolho essa referência para orientar o estudo, mas esta versão não possui comentários "
        "doutrinários homologados nem todas as edições canônicas. O texto citado mantém "
        "a tradução e a fonte identificadas; não vou apresentar uma interpretação particular "
        "como consenso da sua tradição."
    )
