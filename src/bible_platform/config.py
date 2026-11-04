from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, SecretStr

AuthMode = Literal["demo", "oidc", "native_session"]


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
    native_registration_enabled: bool = False
    native_password_reset_enabled: bool = False
    native_session_cookie_name: str = "__Host-bible_session"
    native_session_ttl_hours: int = 12
    native_session_secret: SecretStr | None = None
    native_public_origin: str | None = None
    native_login_max_attempts: int = 8
    native_login_window_minutes: int = 15
    native_reset_ttl_minutes: int = 20
    native_smtp_host: str | None = None
    native_smtp_port: int = 587
    native_smtp_user: str | None = None
    native_smtp_password: SecretStr | None = None
    native_smtp_from: str | None = None

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
        if self.production and self.auth_mode != "native_session":
            raise ValueError("BIBLE_PRODUCTION_REQUIRES_NATIVE_SESSION")
        if self.auth_mode == "native_session":
            if not self.database_url:
                raise ValueError("BIBLE_NATIVE_DATABASE_REQUIRED")
            if not self.native_session_secret or len(self.native_session_secret.get_secret_value()) < 32:
                raise ValueError("BIBLE_NATIVE_SESSION_SECRET_REQUIRED")
            if self.native_session_ttl_hours < 1 or self.native_session_ttl_hours > 168:
                raise ValueError("BIBLE_NATIVE_SESSION_TTL_INVALID")
            if self.production:
                if self.native_session_cookie_name != "__Host-bible_session":
                    raise ValueError("BIBLE_NATIVE_HOST_COOKIE_REQUIRED")
                from urllib.parse import urlparse
                origin = urlparse(self.native_public_origin or "")
                if origin.scheme != "https" or not origin.netloc or origin.path not in ("", "/") or origin.username or origin.password or origin.query or origin.fragment:
                    raise ValueError("BIBLE_NATIVE_PUBLIC_ORIGIN_INVALID")
            if self.native_password_reset_enabled and not all((
                self.native_smtp_host, self.native_smtp_user, self.native_smtp_password, self.native_smtp_from
            )):
                raise ValueError("BIBLE_NATIVE_SMTP_REQUIRED")
        if self.production and not self.database_url and self.database_required:
            raise ValueError("BIBLE_PRODUCTION_DATABASE_REQUIRED")
        if self.production and self.database_url:
            from sqlalchemy.engine import make_url
            try:
                if make_url(self.database_url).get_backend_name() != "postgresql":
                    raise ValueError("BIBLE_PRODUCTION_POSTGRESQL_REQUIRED")
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError("BIBLE_PRODUCTION_DATABASE_URL_INVALID") from exc
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
        native_registration_enabled=env_bool("BIBLE_NATIVE_REGISTRATION_ENABLED", False),
        native_password_reset_enabled=env_bool("BIBLE_NATIVE_PASSWORD_RESET_ENABLED", False),
        native_session_cookie_name=os.getenv("BIBLE_NATIVE_SESSION_COOKIE_NAME", "__Host-bible_session"),
        native_session_ttl_hours=int(os.getenv("BIBLE_NATIVE_SESSION_TTL_HOURS", "12")),
        native_session_secret=SecretStr(os.environ["BIBLE_NATIVE_SESSION_SECRET"]) if os.getenv("BIBLE_NATIVE_SESSION_SECRET") else None,
        native_public_origin=(os.getenv("BIBLE_NATIVE_PUBLIC_ORIGIN") or "").rstrip("/") or None,
        native_login_max_attempts=int(os.getenv("BIBLE_NATIVE_LOGIN_MAX_ATTEMPTS", "8")),
        native_login_window_minutes=int(os.getenv("BIBLE_NATIVE_LOGIN_WINDOW_MINUTES", "15")),
        native_reset_ttl_minutes=int(os.getenv("BIBLE_NATIVE_RESET_TTL_MINUTES", "20")),
        native_smtp_host=os.getenv("BIBLE_NATIVE_SMTP_HOST") or None,
        native_smtp_port=int(os.getenv("BIBLE_NATIVE_SMTP_PORT", "587")),
        native_smtp_user=os.getenv("BIBLE_NATIVE_SMTP_USER") or None,
        native_smtp_password=SecretStr(os.environ["BIBLE_NATIVE_SMTP_PASSWORD"]) if os.getenv("BIBLE_NATIVE_SMTP_PASSWORD") else None,
        native_smtp_from=os.getenv("BIBLE_NATIVE_SMTP_FROM") or None,
        content_source_id=os.getenv("BIBLE_CONTENT_SOURCE_ID", "bible-livre"),
        content_source_version=os.getenv("BIBLE_CONTENT_SOURCE_VERSION", "2025.1.0"),
        rate_limit_per_minute=int(os.getenv("BIBLE_RATE_LIMIT_PER_MINUTE", "60")),
        docs_enabled=env_bool("BIBLE_DOCS_ENABLED", False),
        log_restricted_content=env_bool("BIBLE_LOG_RESTRICTED_CONTENT", False),
    )
    settings.validate_runtime()
    return settings
