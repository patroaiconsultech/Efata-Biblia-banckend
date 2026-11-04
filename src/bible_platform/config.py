from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, SecretStr

AuthMode = Literal["demo", "oidc"]


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


class Settings(BaseModel):
    app_name: str = "EfaTÀ 777 — Bíblia Digital"
    version: str = "0.14.0-audit"
    environment: str = "local"
    release_sha: str = "UNPINNED"
    auth_mode: AuthMode = "demo"
    allowed_origins: tuple[str, ...] = ("http://localhost:5173",)
    database_url: str | None = None
    database_required: bool = True
    demo_allowed_tenants: tuple[str, ...] = ("tenant-demo",)
    demo_allowed_users: tuple[str, ...] = ("user-demo",)
    demo_admin_users: tuple[str, ...] = ()
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_introspection_endpoint: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_user_claim: str = "sub"
    oidc_tenant_claim: str = "tenant_id"
    oidc_roles_claim: str = "roles"
    restricted_data_key: SecretStr | None = None
    content_source_id: str = "bible-livre"
    content_source_version: str = "2025.1.0"
    rate_limit_per_minute: int = 60
    docs_enabled: bool = False
    log_restricted_content: bool = False

    @property
    def production(self) -> bool:
        return self.environment.lower() in {"production", "staging"}

    @property
    def oidc_configured(self) -> bool:
        return all((self.oidc_issuer, self.oidc_audience, self.oidc_introspection_endpoint, self.oidc_client_id, self.oidc_client_secret))

    def validate_runtime(self) -> None:
        if self.production and self.auth_mode != "oidc":
            raise ValueError("BIBLE_PRODUCTION_REQUIRES_OIDC")
        if self.production and not self.database_url and self.database_required:
            raise ValueError("BIBLE_PRODUCTION_DATABASE_REQUIRED")
        if self.auth_mode == "oidc" and not self.oidc_configured:
            raise ValueError("BIBLE_OIDC_CONFIGURATION_INCOMPLETE")
        if self.production and not self.restricted_data_key:
            raise ValueError("BIBLE_RESTRICTED_DATA_KEY_REQUIRED")
        if "*" in self.allowed_origins and self.production:
            raise ValueError("BIBLE_CORS_WILDCARD_FORBIDDEN")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    raw_key = os.getenv("BIBLE_RESTRICTED_DATA_KEY")
    settings = Settings(
        environment=os.getenv("BIBLE_ENVIRONMENT", "local").strip().lower(),
        release_sha=os.getenv("BIBLE_RELEASE_SHA", "UNPINNED"),
        auth_mode=os.getenv("BIBLE_AUTH_MODE", "demo").strip().lower(),
        allowed_origins=env_csv("BIBLE_ALLOWED_ORIGINS", ("http://localhost:5173",)),
        database_url=os.getenv("DATABASE_URL") or None,
        database_required=env_bool("BIBLE_DATABASE_REQUIRED", True),
        demo_allowed_tenants=env_csv("BIBLE_DEMO_ALLOWED_TENANTS", ("tenant-demo",)),
        demo_allowed_users=env_csv("BIBLE_DEMO_ALLOWED_USERS", ("user-demo",)),
        demo_admin_users=env_csv("BIBLE_DEMO_ADMIN_USERS", ()),
        oidc_issuer=os.getenv("BIBLE_OIDC_ISSUER") or None,
        oidc_audience=os.getenv("BIBLE_OIDC_AUDIENCE") or None,
        oidc_introspection_endpoint=os.getenv("BIBLE_OIDC_INTROSPECTION_ENDPOINT") or None,
        oidc_client_id=os.getenv("BIBLE_OIDC_CLIENT_ID") or None,
        oidc_client_secret=SecretStr(os.getenv("BIBLE_OIDC_CLIENT_SECRET")) if os.getenv("BIBLE_OIDC_CLIENT_SECRET") else None,
        oidc_user_claim=os.getenv("BIBLE_OIDC_USER_CLAIM", "sub"),
        oidc_tenant_claim=os.getenv("BIBLE_OIDC_TENANT_CLAIM", "tenant_id"),
        oidc_roles_claim=os.getenv("BIBLE_OIDC_ROLES_CLAIM", "roles"),
        restricted_data_key=SecretStr(raw_key) if raw_key else None,
        content_source_id=os.getenv("BIBLE_CONTENT_SOURCE_ID", "bible-livre"),
        content_source_version=os.getenv("BIBLE_CONTENT_SOURCE_VERSION", "2025.1.0"),
        rate_limit_per_minute=int(os.getenv("BIBLE_RATE_LIMIT_PER_MINUTE", "60")),
        docs_enabled=env_bool("BIBLE_DOCS_ENABLED", False),
        log_restricted_content=env_bool("BIBLE_LOG_RESTRICTED_CONTENT", False),
    )
    settings.validate_runtime()
    return settings
