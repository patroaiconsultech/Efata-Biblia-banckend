from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class OrkioExecutionRequest:
    tenant_id: str
    user_id: str
    thread_id: str
    agent_id: str = "bible_guide"
    prompt: str = ""


class OrkioUnavailable(RuntimeError):
    pass


class OrkioAdapter:
    """Boundary HTTP-only for optional ORKIO capabilities; never imports ORKIO internals."""

    def __init__(self, base_url: str | None = None, service_token: str | None = None, timeout: float = 5.0, failure_threshold: int = 3) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.service_token = service_token
        self.timeout = timeout
        self.failure_threshold = failure_threshold
        self.failures = 0
        self.opened_at: float | None = None

    def enabled(self) -> bool:
        return bool(self.base_url and self.service_token)

    def _allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at > 30:
            self.opened_at = None
            self.failures = 0
            return True
        return False

    def request_execution(self, request: OrkioExecutionRequest) -> dict[str, Any]:
        if not self.enabled() or not self._allow():
            raise OrkioUnavailable("ORKIO_ADAPTER_DISABLED_OR_CIRCUIT_OPEN")
        if request.agent_id != "bible_guide":
            raise OrkioUnavailable("ORKIO_AGENT_OWNERSHIP_REJECTED")
        try:
            response = httpx.post(
                f"{self.base_url}/api/executions",
                headers={"Authorization": f"Bearer {self.service_token}", "X-Tenant-ID": request.tenant_id, "X-Request-Owner": request.agent_id},
                json={"tenant_id": request.tenant_id, "user_id": request.user_id, "thread_id": request.thread_id, "agent_id": request.agent_id, "prompt": request.prompt},
                timeout=self.timeout,
            )
            response.raise_for_status()
            self.failures = 0
            payload = response.json()
            if payload.get("tenant_id") != request.tenant_id or payload.get("agent_id") != request.agent_id:
                raise OrkioUnavailable("ORKIO_RESPONSE_CONTEXT_MISMATCH")
            return payload
        except (httpx.HTTPError, ValueError, OrkioUnavailable) as exc:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.opened_at = time.monotonic()
            if isinstance(exc, OrkioUnavailable):
                raise
            raise OrkioUnavailable("ORKIO_REQUEST_FAILED") from exc
