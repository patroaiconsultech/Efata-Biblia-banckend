from fastapi import APIRouter, Response

from bible_platform.config import get_settings
from bible_platform.content.rights import rights_service
from bible_platform.infrastructure.store import store

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict[str, object]:
    settings = get_settings()
    return {
        "status": "ok",
        "application": settings.app_name,
        "version": settings.version,
        "release_sha": settings.release_sha,
        "environment": settings.environment,
        "database_backend": store.backend_name,
        "auth_mode": settings.auth_mode,
        "content_source": settings.content_source_id,
        "content_source_version": settings.content_source_version,
        "encryption_key_version": "v1",
    }


@router.get("/readiness")
def readiness(response: Response) -> dict[str, object]:
    settings = get_settings()
    database_ready = False
    schema_ready = False
    try:
        database_ready = store.ping()
        schema_ready = store.schema_ready()
    except Exception:
        database_ready = False
        schema_ready = False

    rights = rights_service.status()
    native_auth_ready = (
        settings.auth_mode == "native_session"
        and bool(settings.native_session_secret)
        and bool(settings.native_public_origin)
        and store.backend_name != "memory"
    )
    auth_ready = (settings.auth_mode == "demo" and not settings.production) or settings.oidc_configured or native_auth_ready
    if not (database_ready and schema_ready and auth_ready):
        response.status_code = 503
    production_ready = bool(
        database_ready
        and schema_ready
        and (store.backend_name != "memory" or not settings.production)
        and auth_ready
        and rights["available"]
        and (rights["publication_ready"] or not settings.production)
    )

    return {
        "status": "ready" if database_ready and schema_ready and auth_ready else "not_ready",
        "database": store.backend_name,
        "database_ready": database_ready,
        "schema_ready": schema_ready,
        "persistent_repository": store.backend_name != "memory",
        "auth_configured": auth_ready,
        "restricted_encryption_configured": bool(settings.restricted_data_key) or not settings.production,
        "rights_registry_available": rights["available"],
        "content_rights_ready": rights["publication_ready"],
        "content_rights_legal_review": rights["legal_review"],
        "content_rights_error_code": rights["error_code"],
        "production_ready": production_ready,
    }
