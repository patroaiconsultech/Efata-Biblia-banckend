"""AI pilot contract tests. No real OpenAI requests or user data."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from bible_platform.ai import guide
from bible_platform.domain_models import BiblePrincipal


def principal():
    return BiblePrincipal(
        tenant_id="tenant-one",
        user_id="user-one",
        role="member",
        issuer="test",
        subject="user-one",
        auth_method="native_session",
    )


def make_client(current_principal=None):
    app = FastAPI()
    app.include_router(guide.router)
    if current_principal is None:
        app.dependency_overrides[guide.get_principal] = principal
    else:
        app.dependency_overrides[guide.get_principal] = lambda: current_principal
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.delenv("BIBLE_AI_ENABLED", raising=False)
    monkeypatch.delenv("BIBLE_AI_PILOT_USER_ID", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BIBLE_OPENAI_MODEL", raising=False)
    monkeypatch.setenv("BIBLE_AI_GLOBAL_DAILY_BUDGET_CENTS", "5")
    monkeypatch.setenv("BIBLE_AI_PILOT_DAILY_CALLS", "5")
    # Authorization/SSE unit tests never require external PostgreSQL and
    # never invoke OpenAI; this is a controlled fake budget adapter.
    claims = []
    finalizations = []
    def fake_reserve(principal, request_id):
        claims.append((principal.user_id, request_id))
        return guide.Reservation("ai_test_" + str(len(claims)))
    def fake_finalize(*args, **kwargs):
        finalizations.append((args, kwargs))
    monkeypatch.setattr(guide, "reserve_budget", fake_reserve)
    monkeypatch.setattr(guide, "finalize_budget", fake_finalize)
    monkeypatch.setattr(guide, "budget_ready", lambda: True)
    monkeypatch.setattr(guide, "_test_claims", claims, raising=False)
    monkeypatch.setattr(guide, "_test_finalizations", finalizations, raising=False)


def test_disabled_by_default_returns_503_not_fake_success():
    response = make_client().post(
        "/api/conversations/main/ai-stream",
        json={
            "thread_id": "main",
            "content": "Como organizar meus estudos?",
            "ai_opt_in": True,
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "BIBLE_AI_DISABLED"


def test_absent_opt_in_is_rejected_before_provider():
    response = make_client().post(
        "/api/conversations/main/ai-stream",
        json={"thread_id": "main", "content": "Como organizar meus estudos?"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_openai_request_is_ephemeral_and_excludes_identity(monkeypatch):
    seen = []
    async def remote_handler(request):
        data = json.loads(request.content)
        seen.append((request, data))
        return httpx.Response(200, json={
            "status": "completed",
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Estude um pouco por dia."}],
            }],
        })
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        guide.httpx, "AsyncClient",
        lambda **kw: original_client(
            transport=httpx.MockTransport(remote_handler), **kw
        ),
    )
    result = await guide.generate_ai_text(
        "Como organizar meus estudos?",
        key="sk-test-should-not-appear-in-output",
        limit=400,
        timeout=8,
    )
    assert result == "Estude um pouco por dia."
    req, body = seen[0]
    assert req.url == "https://api.openai.com/v1/responses"
    assert req.headers["Authorization"].startswith("Bearer sk-")
    assert body["model"] == "gpt-6-luna"
    assert body["store"] is False
    assert body["reasoning"]["effort"] == "none"
    assert body["input"] == "Como organizar meus estudos?"
    for forbidden in ("tenant_id", "user_id", "history", "journal", "tools", "conversation"):
        assert forbidden not in body
    assert "sk-test" not in result


@pytest.mark.asyncio
async def test_ai_sse_contract_and_no_storage(monkeypatch):
    async def fake_generate(*args, **kwargs):
        kwargs["usage"].update(input_tokens=120, output_tokens=20)
        return "Resposta fictícia de teste."
    monkeypatch.setattr(guide, "generate_ai_text", fake_generate)
    req = guide.AiRequest(
        thread_id="main",
        content="Como criar uma rotina?",
        ai_opt_in=True,
        request_id="web_test_01",
    )
    output = [
        json.loads(fragment.split("data: ", 1)[1].split("\n\n", 1)[0])
        async for fragment in guide._ai_events(
            req, principal(), key="sk-test", limit=400, timeout=8,
            reservation=guide.Reservation("ai_test")
        )
    ]
    assert [e["event_type"] for e in output] == [
        "meta", "content.delta", "content.final", "done",
    ]
    assert [e["sequence"] for e in output] == [1, 2, 3, 4]
    assert output[0]["request_id"] == "web_test_01"
    assert output[0]["tenant_id"] == "tenant-one"
    assert output[-1]["payload"]["outcome"] == "success"
    assert output[-1]["payload"]["done_observed"] is True
    assert "user-one" not in str(output)


@pytest.mark.asyncio
async def test_provider_error_is_explicit_not_fallback(monkeypatch):
    async def fail(*args, **kwargs):
        raise guide.AiProviderUnavailable("test")
    monkeypatch.setattr(guide, "generate_ai_text", fail)
    req = guide.AiRequest(thread_id="main", content="Pergunta", ai_opt_in=True)
    events = [
        json.loads(chunk.split("data: ", 1)[1].split("\n\n", 1)[0])
        async for chunk in guide._ai_events(
            req, principal(), key="sk-test", limit=400, timeout=8,
            reservation=guide.Reservation("ai_test")
        )
    ]
    assert [e["event_type"] for e in events] == ["meta", "error", "done"]
    assert events[-1]["payload"]["outcome"] == "error"


def test_production_pilot_user_is_required(monkeypatch):
    monkeypatch.setenv("BIBLE_AI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "a" * 24)
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "production")
    response = make_client().post(
        "/api/conversations/main/ai-stream",
        json={
            "thread_id": "main",
            "content": "Como organizar meu estudo?",
            "ai_opt_in": True,
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "BIBLE_AI_PILOT_ONLY"


def test_ai_capability_fail_closed_without_flag():
    response = make_client().get("/api/conversations/ai-status")
    assert response.status_code == 200
    assert response.json() == {"enabled": False}


def _enable_pilot_config(monkeypatch, pilot="user-one"):
    monkeypatch.setenv("BIBLE_AI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "a" * 24)
    if pilot is not None:
        monkeypatch.setenv("BIBLE_AI_PILOT_USER_ID", pilot)
    monkeypatch.setattr(
        guide, "resolve_intent",
        lambda *args, **kwargs: SimpleNamespace(intent="GENERAL_STUDY"),
    )


def _principal_for(user_id="user-one", auth_method="native_session"):
    return BiblePrincipal(
        tenant_id="tenant-one",
        user_id=user_id,
        role="member",
        issuer="test",
        subject=user_id,
        auth_method=auth_method,
    )


def _request_body():
    return {
        "thread_id": "main",
        "content": "Como posso organizar meus estudos semanais?",
        "ai_opt_in": True,
    }


@pytest.mark.parametrize("environment", ["local", "staging", "production"])
@pytest.mark.parametrize(
    "pilot,user_id,auth_method,denial",
    [
        (None, "user-one", "native_session", "BIBLE_AI_PILOT_ONLY"),
        ("user-two", "user-one", "native_session", "BIBLE_AI_PILOT_ONLY"),
        ("user-one", "user-one", "demo", "BIBLE_AI_NATIVE_AUTH_REQUIRED"),
        ("user-one", "user-one", "oidc", "BIBLE_AI_NATIVE_AUTH_REQUIRED"),
    ],
)
def test_rejected_pilot_never_reaches_provider_or_allocates_quota(
    monkeypatch, environment, pilot, user_id, auth_method, denial,
):
    """Production, staging and local must enforce identical paid-call auth."""
    monkeypatch.setenv("BIBLE_ENVIRONMENT", environment)
    _enable_pilot_config(monkeypatch, pilot=pilot)
    provider_calls = []

    async def provider_spy(*args, **kwargs):
        provider_calls.append((args, kwargs))
        return "Unexpected provider call."

    monkeypatch.setattr(guide, "generate_ai_text", provider_spy)
    client = make_client(_principal_for(user_id, auth_method))
    status = client.get("/api/conversations/ai-status")
    assert status.status_code == 200
    assert status.json() == {"enabled": False}

    response = client.post(
        "/api/conversations/main/ai-stream",
        json=_request_body(),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == denial
    assert provider_calls == []
    assert guide._test_claims == []


@pytest.mark.parametrize("environment", ["local", "staging", "production"])
def test_authorized_native_pilot_can_complete_with_fake_provider(
    monkeypatch, environment,
):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", environment)
    _enable_pilot_config(monkeypatch)
    provider_calls = []

    async def provider_spy(*args, **kwargs):
        provider_calls.append(1)
        kwargs["usage"].update(input_tokens=120, output_tokens=20)
        return "Resposta de teste sem provedor externo."

    monkeypatch.setattr(guide, "generate_ai_text", provider_spy)
    client = make_client(_principal_for())
    status = client.get("/api/conversations/ai-status")
    assert status.status_code == 200
    assert status.json()["enabled"] is True
    response = client.post(
        "/api/conversations/main/ai-stream", json=_request_body()
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: done" in response.text
    assert len(provider_calls) == 1
    assert len(guide._test_claims) == 1
    assert len(guide._test_finalizations) == 1


@pytest.mark.asyncio
async def test_quota_guard_rechecks_native_pilot(monkeypatch):
    _enable_pilot_config(monkeypatch)
    for rejected in (
        _principal_for("user-other", "native_session"),
        _principal_for("user-one", "demo"),
        _principal_for("user-one", "oidc"),
    ):
        with pytest.raises(HTTPException) as caught:
            await guide._claim_slot(rejected, "test_req_01")
        assert caught.value.status_code == 403
    assert guide._test_claims == []


def test_ai_status_hides_capability_when_budget_unavailable(monkeypatch):
    _enable_pilot_config(monkeypatch)
    monkeypatch.setattr(guide, "budget_ready", lambda: False)
    res = make_client().get("/api/conversations/ai-status")
    assert res.status_code == 200
    assert res.json() == {"enabled": False}


@pytest.mark.asyncio
async def test_provider_usage_is_collected_without_logging_private_text(monkeypatch):
    async def handler(request):
        return httpx.Response(200, json={
            "status": "completed",
            "output": [{
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Teste."}],
            }],
            "usage": {"input_tokens": 144, "output_tokens": 12,
                      "total_tokens": 156},
        })
    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        guide.httpx, "AsyncClient",
        lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw)
    )
    usage = {}
    answer = await guide.generate_ai_text(
        "Pergunta privada somente no teste", key="sk-" + "x" * 30,
        limit=400, timeout=8, usage=usage,
    )
    assert answer == "Teste."
    assert usage == {"input_tokens": 144, "output_tokens": 12}


@pytest.mark.asyncio
async def test_provider_missing_usage_fails_closed(monkeypatch):
    async def handler(request):
        return httpx.Response(200, json={
            "status": "completed",
            "output": [{
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Teste."}],
            }],
        })
    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        guide.httpx, "AsyncClient",
        lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw)
    )
    with pytest.raises(guide.AiProviderUnavailable, match="USAGE_MISSING"):
        await guide.generate_ai_text(
            "Teste", key="sk-" + "x" * 30,
            limit=400, timeout=8, usage={},
        )
