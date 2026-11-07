"""Isolated optional AI guide, retaining bible.sse.v1 and the native session.

This route deliberately does NOT read/persist conversations, journals, faith
preferences or the licensed corpus. Only the explicitly opted-in typed question
is sent to OpenAI. Production use is restricted to a named pilot user.
"""
from __future__ import annotations

import asyncio
import json
import logging
from time import monotonic
import os
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ConfigDict, Field

from bible_platform.api.dependencies import get_principal
from bible_platform.ai.budget import (
    BudgetUnavailable, Reservation, budget_ready, budget_settings,
    reserve_budget, finalize_budget,
)
from bible_platform.behavior.intent import parse_reference, resolve_intent
from bible_platform.domain_models import (
    BiblePrincipal, ContentBlock, ConversationRequest, new_id, now,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations-ai"])
log = logging.getLogger("bible.ai.guide")
_API_URL = "https://api.openai.com/v1/responses"
_ALLOWED_MODEL = "gpt-6-luna"
_DENIED_INTENTS = frozenset({
    "CRISIS_SUPPORT", "SPIRITUAL_GUILT_SUPPORT", "PROFESSIONAL_BOUNDARY",
    "PERSONAL_PROPHECY", "COERCION_GUARD", "SCRIPTURE_READING",
    "HISTORICAL_RESEARCH", "TRADITION_COMPARISON",
})
_GUIDANCE = (
    "Você é um auxiliar de estudos e reflexões em português do Brasil. "
    "Não se apresente como Deus, profeta, pastor, terapeuta ou autoridade jurídica. "
    "Não invente fontes, citações, versículos, traduções, datas ou pesquisas. "
    "Não reproduza texto do corpus bíblico nem afirme tê-lo consultado. "
    "Se o usuário precisar de uma citação textual, explique que a fonte está pendente "
    "de revisão e recomende consulta direta a uma edição devidamente licenciada. "
    "Diferencie fato, possibilidade e interpretação. Respeite tradições diferentes. "
    "Não pressione ninguém com culpa, medo ou autoridade divina. "
    "Não peça dados pessoais sensíveis. Seja conciso e explique limitações."
)

class AiRequest(ConversationRequest):
    model_config = ConfigDict(extra="forbid")
    # The browser must explicitly opt into each individual model call.
    content: str = Field(min_length=1, max_length=1200)
    ai_opt_in: Literal[True]
    request_id: str | None = Field(default=None, max_length=120)


class AiProviderUnavailable(RuntimeError):
    """A neutral error that cannot leak remote response bodies or secrets."""


def _bool_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _config() -> tuple[str, int, float]:
    # No request is allowed simply because OPENAI_API_KEY exists.
    if not _bool_env("BIBLE_AI_ENABLED"):
        raise HTTPException(503, detail={"code": "BIBLE_AI_DISABLED"})
    key = os.getenv("OPENAI_API_KEY", "")
    if not key.startswith("sk-") or len(key) < 20:
        raise HTTPException(503, detail={"code": "BIBLE_AI_KEY_NOT_CONFIGURED"})
    model = os.getenv("BIBLE_OPENAI_MODEL", _ALLOWED_MODEL).strip()
    if model != _ALLOWED_MODEL:
        raise HTTPException(503, detail={"code": "BIBLE_AI_MODEL_NOT_APPROVED"})
    try:
        limit = int(os.getenv("BIBLE_OPENAI_MAX_OUTPUT_TOKENS", "400"))
        timeout = float(os.getenv("BIBLE_OPENAI_TIMEOUT_SECONDS", "30"))
    except ValueError as exc:
        raise HTTPException(503, detail={"code": "BIBLE_AI_LIMIT_INVALID"}) from exc
    if not (64 <= limit <= 600 and 5 <= timeout <= 40):
        raise HTTPException(503, detail={"code": "BIBLE_AI_LIMIT_INVALID"})
    return key, limit, timeout


def _pilot_denial_code(principal: BiblePrincipal) -> str | None:
    """One fail-closed permission rule for capability and execution.

    Production, staging and local deployments all require the same native
    session and an explicitly configured user allowlist. Never trust a user
    ID, tenant ID or role supplied directly by the browser.
    """
    if principal.auth_method != "native_session":
        return "BIBLE_AI_NATIVE_AUTH_REQUIRED"
    pilot = os.getenv("BIBLE_AI_PILOT_USER_ID", "").strip()
    if not pilot or not principal.user_id or principal.user_id != pilot:
        return "BIBLE_AI_PILOT_ONLY"
    return None


def _require_pilot(principal: BiblePrincipal) -> None:
    code = _pilot_denial_code(principal)
    if code is not None:
        raise HTTPException(403, detail={"code": code})


async def _claim_slot(principal: BiblePrincipal, request_id: str) -> Reservation:
    # Recheck authorization before ANY paid provider call, in every environment.
    _require_pilot(principal)
    try:
        return await asyncio.to_thread(reserve_budget, principal, request_id)
    except BudgetUnavailable as exc:
        raise HTTPException(
            503, detail={"code": "BIBLE_AI_BUDGET_UNAVAILABLE"}
        ) from exc


async def generate_ai_text(question: str, *, key: str, limit: int,
                           timeout: float, usage: dict[str, int] | None = None) -> str:
    # No tenant/user ID, token, diary, conversation history or corpus is sent.
    # The provider receives only one question explicitly approved by its author.
    body = {
        "model": _ALLOWED_MODEL,
        "store": False,
        "reasoning": {"effort": "none"},
        "instructions": _GUIDANCE,
        "input": question,
        "max_output_tokens": limit,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                _API_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AiProviderUnavailable("BIBLE_AI_PROVIDER_UNAVAILABLE") from exc
    if data.get("status") != "completed":
        raise AiProviderUnavailable("BIBLE_AI_INCOMPLETE")
    parts = [
        part.get("text", "")
        for item in data.get("output", [])
        if item.get("type") == "message" and item.get("role") == "assistant"
        for part in item.get("content", [])
        if part.get("type") == "output_text" and isinstance(part.get("text"), str)
    ]
    answer = "\n".join(p for p in parts if p.strip()).strip()
    if not answer or len(answer) > 6000:
        raise AiProviderUnavailable("BIBLE_AI_OUTPUT_INVALID")
    if usage is not None:
        provider_usage = data.get("usage")
        if not isinstance(provider_usage, dict):
            raise AiProviderUnavailable("BIBLE_AI_USAGE_MISSING")
        inputs = provider_usage.get("input_tokens")
        outputs = provider_usage.get("output_tokens")
        if (type(inputs) is not int or type(outputs) is not int or
                inputs < 0 or outputs < 0 or outputs > limit):
            raise AiProviderUnavailable("BIBLE_AI_USAGE_INVALID")
        usage["input_tokens"] = inputs
        usage["output_tokens"] = outputs
    return answer


def _sse(event: dict[str, object]) -> str:
    return f"event: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _ai_events(
    request: AiRequest, principal: BiblePrincipal, *,
    key: str, limit: int, timeout: float, reservation: Reservation,
):
    request_id = request.request_id or new_id("req")
    execution_id = new_id("exe")
    sequence = 0

    def event(kind: str, payload: dict[str, object]) -> str:
        nonlocal sequence
        sequence += 1
        return _sse({
            "schema_version": "bible.sse.v1",
            "event_id": new_id("evt"),
            "event_type": kind,
            "request_id": request_id,
            "execution_id": execution_id,
            "tenant_id": principal.tenant_id,
            "thread_id": request.thread_id,
            "agent_id": "bible_guide",
            "turn_owner": "bible_guide",
            "sequence": sequence,
            "payload": payload,
            "created_at": now().isoformat(),
        })

    yield event("meta", {
        "mode": request.mode,
        "primary_intent": "AI_EXPLICIT_OPT_IN",
        "policy_ids": ["BIBLE-AI-OPTIN-V1", "BIBLE-NO-UNLICENSED-CORPUS"],
        "behavior_version": "bible.guide.v2-openai-pilot",
        "model": _ALLOWED_MODEL,
        "raw_user_content_persisted": False,
        "done_observed": False,
        "transport": "sse",
    })
    completed_budget = False
    started = monotonic()
    try:
        usage: dict[str, int] = {}
        answer = await generate_ai_text(
            request.content, key=key, limit=limit, timeout=timeout,
            usage=usage,
        )
        # Never send success before the budget ledger is durably updated.
        await asyncio.to_thread(
            finalize_budget, reservation, status="completed",
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )
        completed_budget = True
        log.info("bible_ai_execution outcome=completed latency_ms=%d",
                 int((monotonic() - started) * 1000))
        block = ContentBlock(block_type="AI_REFLECTION", content=answer)
        message_id = new_id("msg")
        # Response is ephemeral: avoid persisting private questions or AI text
        # in the current unencrypted conversation repository.
        yield event("content.delta", {
            "block_type": block.block_type,
            "delta": block.content,
            "citations": [],
        })
        yield event("content.final", {
            "message_id": message_id,
            "blocks": [block.model_dump()],
        })
        yield event("done", {
            "outcome": "success", "done_observed": True,
            "terminal_source": "wire", "message_id": message_id,
        })
    except asyncio.CancelledError:
        log.info("bible_ai_execution outcome=cancelled latency_ms=%d",
                 int((monotonic() - started) * 1000))
        # Provider can bill even on disconnect; never release its reservation.
        if not completed_budget:
            try:
                await asyncio.to_thread(
                    finalize_budget, reservation, status="unknown"
                )
            except BudgetUnavailable:
                pass
        # Client disconnects might prevent delivery of any terminal event.
        yield event("cancelled", {"reason": "client_disconnected"})
        yield event("done", {
            "outcome": "cancelled", "done_observed": True,
            "terminal_source": "wire",
        })
    except Exception as exc:
        log.warning("bible_ai_execution outcome=error error_class=%s latency_ms=%d",
                    type(exc).__name__, int((monotonic() - started) * 1000))
        if not completed_budget:
            try:
                await asyncio.to_thread(
                    finalize_budget, reservation, status="unknown"
                )
            except BudgetUnavailable:
                pass
        # Never fall back to a synthetic success or leak provider errors.
        yield event("error", {
            "code": "BIBLE_AI_PROVIDER_UNAVAILABLE",
            "message": "Não foi possível consultar a IA agora.",
        })
        yield event("done", {
            "outcome": "error", "done_observed": True,
            "terminal_source": "wire",
        })


@router.get("/ai-status")
def ai_status(principal: BiblePrincipal = Depends(get_principal)) -> dict[str, object]:
    """Capability exposure only; does not authorize a model call."""
    if _pilot_denial_code(principal) is not None:
        return {"enabled": False}
    try:
        _config()
    except HTTPException:
        return {"enabled": False}
    if not budget_ready():
        return {"enabled": False}
    return {"enabled": True, "model": _ALLOWED_MODEL, "pilot_limit_per_day": 5}


@router.post("/{thread_id}/ai-stream")
async def stream_ai_conversation(
    thread_id: str,
    request: AiRequest,
    principal: BiblePrincipal = Depends(get_principal),
) -> StreamingResponse:
    if thread_id != request.thread_id or thread_id != "main":
        raise HTTPException(400, detail={"code": "BIBLE_AI_THREAD_INVALID"})
    # Keep the existing disabled-by-default 503 contract, then enforce
    # pilot authorization in every environment before intent or provider use.
    key, limit, timeout = _config()
    _require_pilot(principal)
    try:
        budget_settings()
    except BudgetUnavailable as exc:
        raise HTTPException(503, detail={"code": "BIBLE_AI_BUDGET_UNAVAILABLE"}) from exc
    decision = resolve_intent(request.content, request.mode)
    # No copyrighted source lookup, RAG or sensitive safety-critical prompts.
    if decision.intent in _DENIED_INTENTS or parse_reference(request.content):
        raise HTTPException(422, detail={"code": "BIBLE_AI_INPUT_NOT_ALLOWED"})
    request_id = request.request_id or new_id("req")
    reservation = await _claim_slot(principal, request_id)
    # The idempotency key used for budget allocation is the same SSE request ID.
    request.request_id = request_id
    return StreamingResponse(
        _ai_events(request, principal, key=key, limit=limit, timeout=timeout,
                   reservation=reservation),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
