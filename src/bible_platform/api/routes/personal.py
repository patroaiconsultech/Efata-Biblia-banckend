from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from bible_platform.api.dependencies import get_principal
from bible_platform.domain_models import BiblePrincipal, HighlightCreate, JournalCreate, PersonalEntry, ProgressRecord, ProgressUpdate, new_id, now
from bible_platform.infrastructure.store import store

router = APIRouter(prefix="/api", tags=["personal"])
Principal = Annotated[BiblePrincipal, Depends(get_principal)]


@router.get("/progress")
def list_progress(principal: Principal) -> dict[str, object]:
    return {"items": [item.model_dump() for item in store.list_progress(principal.tenant_id, principal.user_id)]}


@router.put("/progress/{text_unit_id}")
def update_progress(text_unit_id: str, payload: ProgressUpdate, principal: Principal) -> dict[str, object]:
    item = store.upsert_progress(ProgressRecord(tenant_id=principal.tenant_id, user_id=principal.user_id, text_unit_id=text_unit_id, completed=payload.completed, updated_at=now()))
    return item.model_dump()


@router.get("/bookmarks")
def list_bookmarks(principal: Principal) -> dict[str, object]:
    return {"text_unit_ids": store.list_bookmarks(principal.tenant_id, principal.user_id)}


@router.put("/bookmarks/{text_unit_id}")
def set_bookmark(text_unit_id: str, principal: Principal, active: bool = True) -> dict[str, object]:
    store.upsert_bookmark(principal.tenant_id, principal.user_id, text_unit_id, active)
    return {"text_unit_id": text_unit_id, "active": active}


@router.get("/highlights")
def list_highlights(principal: Principal) -> dict[str, object]:
    return {"items": store.list_highlights(principal.tenant_id, principal.user_id)}


@router.post("/highlights", status_code=201)
def create_highlight(payload: HighlightCreate, principal: Principal) -> dict[str, object]:
    return store.add_highlight(principal.tenant_id, principal.user_id, new_id("highlight"), payload.text_unit_id, payload.note, payload.color)


@router.delete("/highlights/{highlight_id}", status_code=204)
def delete_highlight(highlight_id: str, principal: Principal) -> None:
    try:
        store.delete_highlight(principal.tenant_id, principal.user_id, highlight_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": str(exc), "message": "Destaque não encontrado."}) from exc


@router.get("/journal")
def list_journal(principal: Principal, kind: str | None = None) -> dict[str, object]:
    if kind not in {None, "journal", "prayer", "reflection"}:
        raise HTTPException(status_code=400, detail={"code": "BIBLE_KIND_INVALID", "message": "Tipo de entrada inválido."})
    return {"items": store.list_personal(principal.tenant_id, principal.user_id, kind)}


@router.post("/journal", status_code=201)
def create_journal(payload: JournalCreate, principal: Principal) -> dict[str, object]:
    entry = store.create_personal(principal.tenant_id, principal.user_id, new_id("entry"), payload.kind, payload.content)
    return {"entry_id": entry.entry_id, "kind": entry.kind, "created_at": entry.created_at, "encrypted": True, "key_version": entry.key_version}


@router.delete("/journal/{entry_id}", status_code=204)
def delete_journal(entry_id: str, principal: Principal) -> None:
    try:
        store.delete_personal(principal.tenant_id, principal.user_id, entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": str(exc), "message": "Entrada não encontrada."}) from exc
