"""Standalone Bible identity: persistent users, opaque sessions and CSRF.

Identity tables are a server-only control plane; no ORKIO or OIDC integration.
Runtime requires a dedicated least-privilege PostgreSQL role.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import lru_cache
import hashlib
import hmac
import re
import secrets
import smtplib
import ssl
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError
from fastapi import HTTPException, Request, Response
from sqlalchemy import and_, create_engine, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from bible_platform.config import get_settings
from bible_platform.domain_models import BiblePrincipal, new_id
from bible_platform.infrastructure.schema import (
    auth_users, auth_sessions, auth_login_attempts, auth_reset_tokens
)

_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2,
                                   hash_len=32, salt_len=16)
_DUMMY_HASH = _password_hasher.hash("only_for_constant_path_not_real_password_2026!")
_EMAIL_RE = re.compile(r"^[a-z0-9][a-z0-9.!#$%&'*+/=?^_`{|}~-]*@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


def now() -> datetime:
    return datetime.now(timezone.utc)


def norm_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or len(email) < 5 or not _EMAIL_RE.fullmatch(email) or ".." in email:
        raise HTTPException(422, detail={"code": "BIBLE_EMAIL_INVALID"})
    return email


def _http(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": code})


@lru_cache(maxsize=8)
def _engine(url: str):
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return create_engine(url, pool_pre_ping=True)


def engine():
    url = get_settings().database_url
    if not url:
        raise _http(503, "BIBLE_NATIVE_DATABASE_UNAVAILABLE")
    try:
        return _engine(url)
    except (SQLAlchemyError, ImportError) as exc:
        raise _http(503, "BIBLE_NATIVE_DATABASE_UNAVAILABLE") from exc


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_csrf(token: str) -> str:
    secret = get_settings().native_session_secret
    if not secret:
        raise _http(503, "BIBLE_NATIVE_AUTH_CONFIGURATION_REQUIRED")
    return hmac.new(secret.get_secret_value().encode("utf-8"),
                    ("csrf:v1:" + token).encode("utf-8"), hashlib.sha256).hexdigest()


def check_origin(request: Request) -> None:
    settings = get_settings()
    expected = settings.native_public_origin
    supplied = request.headers.get("origin")
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise _http(403, "BIBLE_ORIGIN_INVALID")
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise _http(403, "BIBLE_CROSS_SITE_REQUEST_DENIED")


def _cookie(request: Request) -> str:
    token = request.cookies.get(get_settings().native_session_cookie_name, "")
    if len(token) < 32 or len(token) > 256:
        raise _http(401, "BIBLE_SESSION_REQUIRED")
    return token


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def resolve_session(request: Request, *, csrf: bool = True) -> tuple[BiblePrincipal, str]:
    token = _cookie(request)
    try:
        with engine().connect() as connection:
            row = connection.execute(
                select(auth_sessions, auth_users.c.role, auth_users.c.status, auth_users.c.tenant_id.label("owner_tenant_id"))
                .join(auth_users, auth_sessions.c.user_id == auth_users.c.user_id)
                .where(auth_sessions.c.session_digest == digest(token))
            ).mappings().first()
    except SQLAlchemyError as exc:
        raise _http(503, "BIBLE_NATIVE_DATABASE_UNAVAILABLE") from exc
    if not row or row["revoked_at"] or _aware(row["expires_at"]) <= now() or row["status"] != "active":
        raise _http(401, "BIBLE_SESSION_INVALID")
    if row["tenant_id"] != row["owner_tenant_id"] or row["role"] not in ("member", "admin", "editor", "researcher"):
        raise _http(403, "BIBLE_AUTH_CONTEXT_INVALID")
    if csrf and request.method not in ("GET", "HEAD", "OPTIONS"):
        check_origin(request)
        provided = request.headers.get("x-csrf-token", "")
        if not provided or not hmac.compare_digest(provided, make_csrf(token)):
            raise _http(403, "BIBLE_CSRF_INVALID")
    principal = BiblePrincipal(
        user_id=row["user_id"], tenant_id=row["tenant_id"],
        role=row["role"], issuer="bible-native",
        subject=row["user_id"], auth_method="native_session",
    )
    return principal, token


def _auth_response(principal: BiblePrincipal, token: str) -> dict:
    return {
        "authenticated": True, "user_id": principal.user_id,
        "tenant_id": principal.tenant_id, "role": principal.role,
        "auth_method": "native_session", "csrf_token": make_csrf(token),
    }


def session_context(request: Request) -> dict:
    principal, token = resolve_session(request, csrf=False)
    return _auth_response(principal, token)


def create_user(email: str, password: str) -> None:
    settings = get_settings()
    if not settings.native_registration_enabled:
        raise _http(403, "BIBLE_REGISTRATION_DISABLED")
    normalized = norm_email(email)
    if not (12 <= len(password) <= 128):
        raise _http(422, "BIBLE_PASSWORD_POLICY_INVALID")
    timestamp = now()
    try:
        with engine().begin() as connection:
            connection.execute(insert(auth_users).values(
                user_id=new_id("usr"), tenant_id=new_id("tenant"),
                email_normalized=normalized,
                password_hash=_password_hasher.hash(password),
                role="member", status="active", created_at=timestamp, updated_at=timestamp,
            ))
    except IntegrityError as exc:
        raise _http(409, "BIBLE_ACCOUNT_UNAVAILABLE") from exc
    except SQLAlchemyError as exc:
        raise _http(503, "BIBLE_NATIVE_DATABASE_UNAVAILABLE") from exc


def _attempt_key(email: str, client_ip: str) -> str:
    return digest(email + "|" + client_ip)


def login(email: str, password: str, client_ip: str, response: Response) -> dict:
    normalized = norm_email(email)
    settings = get_settings()
    attempt_key = _attempt_key(normalized, client_ip)
    timestamp = now()
    try:
        with engine().begin() as connection:
            if connection.dialect.name == "postgresql":
                # Serialize attempts per account+source within the transaction.
                connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": attempt_key})
            recent = connection.execute(select(func.count()).select_from(auth_login_attempts).where(
                and_(auth_login_attempts.c.attempt_key == attempt_key,
                     auth_login_attempts.c.created_at >= timestamp - timedelta(minutes=settings.native_login_window_minutes))
            )).scalar_one()
            if recent >= settings.native_login_max_attempts:
                raise _http(429, "BIBLE_LOGIN_RATE_LIMITED")
            row = connection.execute(select(auth_users).where(
                auth_users.c.email_normalized == normalized
            )).mappings().first()
            try:
                _password_hasher.verify(row["password_hash"] if row else _DUMMY_HASH, password)
                valid = row is not None and row["status"] == "active"
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                valid = False
            if not valid:
                connection.execute(insert(auth_login_attempts).values(
                    attempt_id=secrets.token_hex(32), attempt_key=attempt_key, created_at=timestamp))
                # Avoid returning while inside a rolled-back transaction.
                result = None
            else:
                token = secrets.token_urlsafe(48)
                connection.execute(insert(auth_sessions).values(
                    session_digest=digest(token), user_id=row["user_id"],
                    tenant_id=row["tenant_id"], created_at=timestamp,
                    expires_at=timestamp + timedelta(hours=settings.native_session_ttl_hours),
                    revoked_at=None,
                ))
                result = (BiblePrincipal(user_id=row["user_id"], tenant_id=row["tenant_id"],
                    role=row["role"], issuer="bible-native", subject=row["user_id"],
                    auth_method="native_session"), token)
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise _http(503, "BIBLE_NATIVE_DATABASE_UNAVAILABLE") from exc
    if result is None:
        raise _http(401, "BIBLE_LOGIN_INVALID")
    principal, token = result
    response.set_cookie(settings.native_session_cookie_name, token,
        max_age=settings.native_session_ttl_hours * 3600, path="/",
        httponly=True, secure=settings.production, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return _auth_response(principal, token)


def logout(request: Request, response: Response) -> dict:
    principal, token = resolve_session(request)
    try:
        with engine().begin() as connection:
            connection.execute(update(auth_sessions).where(and_(
                auth_sessions.c.session_digest == digest(token),
                auth_sessions.c.user_id == principal.user_id,
                auth_sessions.c.tenant_id == principal.tenant_id,
            )).values(revoked_at=now()))
    except SQLAlchemyError as exc:
        raise _http(503, "BIBLE_NATIVE_DATABASE_UNAVAILABLE") from exc
    response.delete_cookie(get_settings().native_session_cookie_name, path="/",
                           secure=get_settings().production, httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}


def reset_request(email: str) -> dict:
    settings = get_settings()
    if not settings.native_password_reset_enabled:
        raise _http(503, "BIBLE_PASSWORD_RESET_UNAVAILABLE")
    normalized = norm_email(email)
    token = secrets.token_urlsafe(48)
    try:
        with engine().begin() as connection:
            user = connection.execute(select(auth_users.c.user_id).where(
                auth_users.c.email_normalized == normalized,
                auth_users.c.status == "active",
            )).first()
            if user:
                connection.execute(insert(auth_reset_tokens).values(
                    token_digest=digest(token), user_id=user[0],
                    created_at=now(), expires_at=now() + timedelta(minutes=settings.native_reset_ttl_minutes),
                    used_at=None,
                ))
        if user:
            message = EmailMessage()
            message["Subject"] = "EFATÀ 777 Bíblia — redefinir senha"
            message["From"] = settings.native_smtp_from
            message["To"] = normalized
            message.set_content(
                "Para redefinir sua senha, abra este endereço (uso único):\n"
                + f"{settings.native_public_origin}/app/?reset_token={quote(token)}\n"
                + f"Este link expira em {settings.native_reset_ttl_minutes} minutos.\n")
            with smtplib.SMTP(settings.native_smtp_host, settings.native_smtp_port, timeout=10) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(settings.native_smtp_user, settings.native_smtp_password.get_secret_value())
                smtp.send_message(message)
    except (SQLAlchemyError, OSError, smtplib.SMTPException) as exc:
        raise _http(503, "BIBLE_PASSWORD_RESET_UNAVAILABLE") from exc
    return {"status": "if_registered_email_will_arrive"}


def reset_confirm(token: str, new_password: str) -> dict:
    if not get_settings().native_password_reset_enabled:
        raise _http(503, "BIBLE_PASSWORD_RESET_UNAVAILABLE")
    if len(token) < 32 or not (12 <= len(new_password) <= 128):
        raise _http(400, "BIBLE_PASSWORD_RESET_INVALID")
    try:
        with engine().begin() as connection:
            row = connection.execute(select(auth_reset_tokens).where(
                auth_reset_tokens.c.token_digest == digest(token)
            ).with_for_update()).mappings().first()
            if not row or row["used_at"] or _aware(row["expires_at"]) <= now():
                raise _http(400, "BIBLE_PASSWORD_RESET_INVALID")
            connection.execute(update(auth_users).where(
                auth_users.c.user_id == row["user_id"]
            ).values(password_hash=_password_hasher.hash(new_password), updated_at=now()))
            connection.execute(update(auth_reset_tokens).where(
                auth_reset_tokens.c.token_digest == digest(token)
            ).values(used_at=now()))
            connection.execute(update(auth_sessions).where(
                auth_sessions.c.user_id == row["user_id"]
            ).values(revoked_at=now()))
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise _http(503, "BIBLE_NATIVE_DATABASE_UNAVAILABLE") from exc
    return {"status": "password_updated"}
