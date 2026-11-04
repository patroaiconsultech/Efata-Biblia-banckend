from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import Header, HTTPException, Request

from bible_platform.config import get_settings
from bible_platform.domain_models import BiblePrincipal, Role


def auth_error(code: str, message: str, status_code: int = 401) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def bearer_value(authorization: str | None) -> str:
    if not authorization:
        raise auth_error("AUTH_TOKEN_REQUIRED", "Um bearer token é necessário.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise auth_error("AUTH_TOKEN_INVALID", "O header Authorization é inválido.")
    return token.strip()


def _claim_value(payload: dict[str, Any], claim: str) -> Any:
    value: Any = payload
    for part in claim.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _normalize_role(value: Any) -> Role:
    roles = value if isinstance(value, list) else [value]
    normalized = {str(role).strip().lower() for role in roles if role}
    for candidate in ("admin", "editor", "researcher", "member"):
        if candidate in normalized:
            return candidate  # type: ignore[return-value]
    if normalized:
        if "bible_admin" in normalized:
            return "admin"
        if "bible_editor" in normalized:
            return "editor"
        if "bible_researcher" in normalized:
            return "researcher"
    return "member"


def _verify_oidc(token: str) -> BiblePrincipal:
    settings = get_settings()
    if not settings.oidc_configured:
        raise auth_error("AUTH_PROVIDER_NOT_CONFIGURED", "O provedor OIDC não está configurado.", 503)
    try:
        response = httpx.post(
            settings.oidc_introspection_endpoint,
            data={"token": token},
            auth=(settings.oidc_client_id, settings.oidc_client_secret.get_secret_value()),  # type: ignore[union-attr]
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise auth_error("AUTH_PROVIDER_UNAVAILABLE", "Não foi possível validar a sessão.", 503) from exc
    if not isinstance(payload, dict) or not payload.get("active"):
        raise auth_error("AUTH_TOKEN_INVALID", "O token não está ativo.")
    issuer = str(payload.get("iss") or settings.oidc_issuer)
    if issuer != settings.oidc_issuer:
        raise auth_error("AUTH_ISSUER_INVALID", "O issuer do token é inválido.")
    audience = payload.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if settings.oidc_audience not in audiences:
        raise auth_error("AUTH_AUDIENCE_INVALID", "A audience do token é inválida.")
    user_id = _claim_value(payload, settings.oidc_user_claim)
    tenant_id = _claim_value(payload, settings.oidc_tenant_claim)
    if not user_id or not tenant_id:
        raise auth_error("AUTH_CONTEXT_REQUIRED", "O token não contém usuário e tenant.")
    return BiblePrincipal(
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        role=_normalize_role(_claim_value(payload, settings.oidc_roles_claim)),
        scopes=tuple(str(item) for item in str(payload.get("scope", "")).split() if item),
        issuer=issuer,
        subject=str(payload.get("sub") or user_id),
        auth_method="oidc",
    )


def get_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header()] = None,
    x_role: Annotated[str | None, Header()] = None,
) -> BiblePrincipal:
    settings = get_settings()
    if settings.auth_mode == "native_session":
        if authorization or x_tenant_id or x_user_id or x_role:
            raise auth_error("AUTH_CONTEXT_CONFLICT", "Identidade enviada pelo navegador é proibida.")
        from bible_platform.native_auth import resolve_session
        return resolve_session(request)[0]
    if settings.auth_mode == "oidc":
        if x_tenant_id or x_user_id or x_role:
            raise auth_error("AUTH_CONTEXT_CONFLICT", "Headers de identidade controlados pelo browser são proibidos.")
        return _verify_oidc(bearer_value(authorization))
    if settings.production:
        raise auth_error("AUTH_PROVIDER_REQUIRED", "Produção exige autenticação nativa própria.", 503)
    if authorization:
        raise auth_error("AUTH_CONTEXT_CONFLICT", "Bearer token não é aceito no modo demo.")
    if x_tenant_id not in settings.demo_allowed_tenants or x_user_id not in settings.demo_allowed_users:
        raise auth_error("AUTH_CONTEXT_INVALID", "A identidade demo não está autorizada.")
    role = (x_role or "member").strip().lower()
    if role not in {"member", "admin", "editor", "researcher"}:
        raise auth_error("AUTH_ROLE_INVALID", "O role informado é inválido.")
    if role == "admin" and x_user_id not in settings.demo_admin_users:
        raise auth_error("ADMIN_ROLE_REQUIRED", "O usuário não possui administração demo.", 403)
    return BiblePrincipal(user_id=x_user_id, tenant_id=x_tenant_id, role=role, auth_method="demo", subject=x_user_id)


def require_editor(principal: BiblePrincipal) -> BiblePrincipal:
    if principal.role not in {"admin", "editor"}:
        raise auth_error("EDITOR_ROLE_REQUIRED", "É necessário o role editor.", 403)
    return principal


def require_admin(principal: BiblePrincipal) -> BiblePrincipal:
    if principal.role != "admin":
        raise auth_error("ADMIN_ROLE_REQUIRED", "É necessário o role admin.", 403)
    return principal
