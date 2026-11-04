"""Authentication routes; staging and production require independent native sessions."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from bible_platform.api.dependencies import get_principal
from bible_platform.config import get_settings
from bible_platform.domain_models import BiblePrincipal
from bible_platform import native_auth

router = APIRouter(prefix="/api/auth", tags=["auth"])
Principal = Annotated[BiblePrincipal, Depends(get_principal)]


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)


class ResetRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)


class ResetConfirm(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    password: str = Field(min_length=12, max_length=128)


def _native():
    if get_settings().auth_mode != "native_session":
        raise native_auth._http(503, "BIBLE_NATIVE_AUTH_NOT_ENABLED")


@router.get("/status")
def auth_status() -> dict[str, object]:
    settings = get_settings()
    return {"status": "authentication_required", "mode": settings.auth_mode,
            "registration_enabled": settings.native_registration_enabled if settings.auth_mode == "native_session" else False,
            "password_reset_enabled": settings.native_password_reset_enabled if settings.auth_mode == "native_session" else False}


@router.post("/register", status_code=201)
def auth_register(data: Credentials, request: Request):
    _native()
    native_auth.check_origin(request)
    native_auth.create_user(data.email, data.password)
    return {"status": "registered", "next": "login"}


@router.post("/login")
def auth_login(data: Credentials, request: Request, response: Response):
    _native()
    native_auth.check_origin(request)
    client_ip = request.client.host if request.client else "unknown"
    return native_auth.login(data.email, data.password, client_ip, response)


@router.get("/session")
def auth_session(request: Request, response: Response):
    _native()
    response.headers["Cache-Control"] = "no-store"
    return native_auth.session_context(request)


@router.post("/logout")
def auth_logout(request: Request, response: Response):
    _native()
    return native_auth.logout(request, response)


@router.post("/password-reset/request")
def auth_reset_request(data: ResetRequest, request: Request):
    _native()
    native_auth.check_origin(request)
    return native_auth.reset_request(data.email)


@router.post("/password-reset/confirm")
def auth_reset_confirm(data: ResetConfirm, request: Request):
    _native()
    native_auth.check_origin(request)
    return native_auth.reset_confirm(data.token, data.password)


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
