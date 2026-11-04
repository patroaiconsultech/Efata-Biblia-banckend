from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query

from bible_platform.content.catalog import all_units, canon_profiles, corpus, find_units, source_registry, translation, units_for_chapter, works
from bible_platform.content.rights import RightsPolicyError, rights_service

router = APIRouter(prefix="/api", tags=["content"])


def _rights_or_http(operation: str):
    try:
        return rights_service.require(operation)  # type: ignore[arg-type]
    except RightsPolicyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def _rights_metadata_or_http():
    try:
        return rights_service.get()
    except RightsPolicyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.get("/corpora")
def list_corpora() -> list[dict[str, object]]:
    return [corpus().model_dump()]


@router.get("/canons")
def list_canons() -> list[dict[str, object]]:
    return [item.model_dump() for item in canon_profiles()]


@router.get("/translations")
def list_translations() -> list[dict[str, object]]:
    item = translation()
    rights = _rights_metadata_or_http()
    return [{
        **item.model_dump(),
        "rights_status": rights.legal_review,
        "source": source_registry().model_dump(),
    }]


@router.get("/sources")
def list_sources() -> list[dict[str, object]]:
    rights = _rights_metadata_or_http()
    return [{**source_registry().model_dump(), "rights": rights.model_dump()}]


@router.get("/works")
def list_works() -> list[dict[str, object]]:
    return [item.model_dump() for item in works()]


@router.get("/works/{work_id}/chapters/{chapter}")
def read_chapter(work_id: str, chapter: int = Path(..., ge=1)) -> dict[str, object]:
    rights = _rights_or_http("READ")
    selected = units_for_chapter(work_id, chapter)
    if not selected:
        raise HTTPException(status_code=404, detail={"code": "BIBLE_CHAPTER_NOT_FOUND", "message": "Capítulo não encontrado."})
    return {
        "work_id": work_id,
        "chapter": chapter,
        "translation": translation().model_dump(),
        "source": source_registry().model_dump(),
        "rights": {
            "record_id": rights.rights_record_id,
            "attribution_required": rights.attribution_required,
            "legal_review": rights.legal_review,
        },
        "units": [item.model_dump() for item in selected],
    }


@router.get("/text-units/{text_unit_id}")
def read_unit(text_unit_id: str) -> dict[str, object]:
    rights = _rights_or_http("READ")
    item = next((unit for unit in all_units() if unit.text_unit_id == text_unit_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "BIBLE_TEXT_UNIT_NOT_FOUND", "message": "Unidade textual não encontrada."})
    if item.source_id != rights.source_id:
        raise HTTPException(status_code=503, detail={"code": "BIBLE_RIGHTS_SOURCE_MISMATCH", "message": "A fonte solicitada não corresponde ao registro de direitos."})
    return {"unit": item.model_dump(), "source": source_registry().model_dump(), "rights": rights.model_dump()}


@router.get("/search")
def search(q: str = Query("", max_length=300), work_id: str | None = None, chapter: int | None = Query(None, ge=1)) -> dict[str, object]:
    _rights_or_http("SEARCH")
    matches = find_units(q, work_id=work_id, chapter=chapter)
    return {
        "query": q,
        "count": len(matches),
        "translation": translation().model_dump(),
        "source": source_registry().model_dump(),
        "results": [item.model_dump() for item in matches],
    }
