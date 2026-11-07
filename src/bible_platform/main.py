from __future__ import annotations

import time
from collections import defaultdict, deque
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from bible_platform.api.routes.auth import router as auth_router
from bible_platform.admin.overview import router as admin_router
from bible_platform.api.routes.chat import router as chat_router
from bible_platform.api.routes.content import router as content_router
from bible_platform.api.routes.health import router as health_router
from bible_platform.api.routes.journeys import router as journeys_router
from bible_platform.api.routes.personal import router as personal_router
from bible_platform.ai.guide import router as ai_router
from bible_platform.billing.routes import router as billing_router
from bible_platform.billing.webhook_inbox import router as billing_webhook_router
from bible_platform.config import get_settings
from bible_platform.faith.profile import router as faith_router
settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)
if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=(
            ["Authorization", "Content-Type", "Last-Event-ID", "X-Request-ID"]
            + ([] if settings.production else ["X-Tenant-ID", "X-User-ID", "X-Role"])
        ),
        expose_headers=["X-Request-ID"],
    )
_requests: dict[str, deque[float]] = defaultdict(deque)

@app.middleware("http")
async def security_and_request_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or f"req_{uuid4().hex}"
    client_key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = _requests[client_key]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= settings.rate_limit_per_minute:
        return JSONResponse(status_code=429, content={"detail": {"code": "BIBLE_RATE_LIMITED", "message": "Limite temporário atingido.", "request_id": request_id}}, headers={"Retry-After": "60", "X-Request-ID": request_id})
    window.append(now)
    try:
        response = await call_next(request)
    except Exception:
        response = JSONResponse(status_code=500, content={"detail": {"code": "BIBLE_INTERNAL_ERROR", "message": "Erro interno.", "request_id": request_id}})
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), payment=(), usb=(), microphone=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    if settings.production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

@app.exception_handler(Exception)
async def safe_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": {"code": "BIBLE_INTERNAL_ERROR", "message": "Erro interno.", "request_id": request.headers.get("x-request-id")}})

app.include_router(health_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(faith_router)
app.include_router(content_router)
app.include_router(journeys_router)
app.include_router(personal_router)
app.include_router(chat_router)
app.include_router(ai_router)
app.include_router(billing_router)
app.include_router(billing_webhook_router)

@app.get("/")
def root() -> dict[str, object]:
    return {"service": "bible-api", "version": settings.version, "status": "ok", "health": "/api/health", "readiness": "/api/readiness"}
