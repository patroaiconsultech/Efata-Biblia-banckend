from typing import Annotated

from fastapi import APIRouter, Depends

from bible_platform.api.dependencies import get_principal
from bible_platform.domain_models import BiblePrincipal

router = APIRouter(prefix="/api/auth", tags=["auth"])
Principal = Annotated[BiblePrincipal, Depends(get_principal)]


@router.get("/status")
def auth_status() -> dict[str, str]:
    return {"status": "authentication_required", "mode": "oidc_or_demo"}


@router.get("/me")
def auth_me(principal: Principal) -> dict[str, object]:
    return {
        "user_id": principal.user_id,
        "tenant_id": principal.tenant_id,
        "role": principal.role,
        "scopes": principal.scopes,
        "issuer": principal.issuer,
        "auth_method": principal.auth_method,
    }
