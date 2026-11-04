from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from bible_platform.api.dependencies import get_principal
from bible_platform.domain_models import BiblePrincipal

router = APIRouter(prefix="/api", tags=["journeys"])
Principal = Annotated[BiblePrincipal, Depends(get_principal)]

PLANS = (
    {"plan_id": "first-steps", "title": "Primeiros passos", "days": 7, "description": "Uma semana de leitura breve, contexto e reflexão."},
    {"plan_id": "genesis-origins", "title": "Origens em Gênesis", "days": 14, "description": "Leitura guiada de trechos iniciais de Gênesis."},
    {"plan_id": "psalms-presence", "title": "Presença nos Salmos", "days": 30, "description": "Um ritmo contemplativo com fontes e atribuição."},
)


@router.get("/reading-plans")
def list_reading_plans() -> dict[str, object]:
    return {"plans": list(PLANS)}


@router.post("/reading-plans/{plan_id}/enroll")
def enroll(plan_id: str, principal: Principal) -> dict[str, object]:
    plan = next((item for item in PLANS if item["plan_id"] == plan_id), None)
    if plan is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail={"code": "BIBLE_PLAN_NOT_FOUND", "message": "Plano não encontrado."})
    return {"status": "enrolled", "plan": plan, "tenant_id": principal.tenant_id, "user_id": principal.user_id}


@router.get("/journeys/today")
def today(principal: Principal) -> dict[str, object]:
    return {
        "journey_id": "daily-bible-001",
        "title": "Uma observação para hoje",
        "prompt": "Leia um capítulo, observe o contexto e registre uma pergunta honesta.",
        "suggested_reference": "Gn.1.1",
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "coercive_streaks": False,
    }
