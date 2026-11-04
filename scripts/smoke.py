from __future__ import annotations

import json
import os
import sys
import time
import urllib.request


BASE = os.getenv("BIBLE_SMOKE_BASE", "http://127.0.0.1:8099")
HEADERS = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-demo", "X-Role": "member"}


def request(path: str, method: str = "GET", body: dict | None = None, headers: dict | None = None):
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=payload, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, response.headers, response.read().decode()


def main() -> int:
    expected = ["/api/health", "/api/readiness", "/api/works", "/api/works/work_gen/chapters/1"]
    for path in expected:
        status, _, body = request(path)
        assert status == 200, (path, status, body[:300])
    status, _, _ = request("/api/auth/me", headers=HEADERS)
    assert status == 200
    status, _, _ = request("/api/journal", method="POST", body={"content": "smoke reservado", "kind": "journal"}, headers=HEADERS)
    assert status == 201
    status, _, body = request("/api/journal", headers=HEADERS)
    assert status == 200 and "smoke reservado" in body
    status, response_headers, body = request("/api/conversations/smoke/stream", method="POST", body={"thread_id": "smoke", "content": "Explique Gênesis 1:1", "mode": "SCRIPTURE_EXPLANATION"}, headers=HEADERS)
    assert status == 200
    assert response_headers.get_content_type() == "text/event-stream"
    assert "event: done" in body and '"done_observed": true' in body
    print("BIBLE_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
