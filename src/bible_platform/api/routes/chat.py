from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from bible_platform.api.dependencies import get_principal
from bible_platform.behavior.intent import IntentDecision, parse_reference, resolve_intent
from bible_platform.content.catalog import units_for_chapter
from bible_platform.content.rights import RightsPolicyError, rights_service
from bible_platform.domain_models import BiblePrincipal, CitationEnvelope, ContentBlock, ConversationMessage, ConversationRequest, new_id, now
from bible_platform.infrastructure.store import store
from bible_platform.faith.profile import get_consenting_preferences, optional_guide_context

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _assistant_blocks(
    request: ConversationRequest,
    decision: IntentDecision,
) -> tuple[list[ContentBlock], list[CitationEnvelope]]:
    """A deterministic, source-bound assistant; NOT a generative/prophetic engine."""
    blocks: list[ContentBlock] = []
    citations: list[CitationEnvelope] = []

    # P0 boundaries are resolved before retrieving or quoting protected texts.
    if decision.intent == "CRISIS_SUPPORT":
        return [ContentBlock(
            block_type="SAFETY_NOTICE",
            content="Sinto muito que esteja passando por isso. Sua segurança vem primeiro. Se você corre perigo imediato, procure o serviço local de emergência agora. Busque alguém de confiança que possa estar com você presencialmente e afaste-se de meios de se machucar. Posso ouvir, mas não substituo apoio humano e profissional.",
        )], []
    if decision.intent == "SPIRITUAL_GUILT_SUPPORT":
        return [ContentBlock(
            block_type="SAFETY_NOTICE",
            content="Não seria responsável afirmar que uma dificuldade ou sofrimento é castigo divino, nem medir sua fé por isso. Podemos examinar a questão com respeito, sem culpa ou julgamentos automáticos.",
        )], []
    if decision.intent == "FAITH_DOUBT":
        return [ContentBlock(
            block_type="AI_REFLECTION",
            content="Ter dúvidas não torna sua pergunta menos legítima. Podemos examinar as fontes e diferentes interpretações com calma, sem ameaças nem pressão para chegar a uma conclusão.",
        )], []
    if decision.intent == "PROFESSIONAL_BOUNDARY":
        return [ContentBlock(
            block_type="SAFETY_NOTICE",
            content="A oração pode acompanhar cuidados, mas não substitui tratamento. Não interrompa medicamentos ou acompanhamento sem conversar com um profissional de saúde qualificado.",
        )], []
    if decision.intent == "PERSONAL_PROPHECY":
        return [ContentBlock(
            block_type="SAFETY_NOTICE",
            content="Não posso produzir profecias pessoais nem afirmar que Deus determinou um resultado ou uma decisão. Posso ajudar a examinar princípios e informações sem transformar uma interpretação em certeza divina.",
        )], []
    if decision.intent == "COERCION_GUARD":
        return [ContentBlock(
            block_type="SAFETY_NOTICE",
            content="Não vou usar textos religiosos para obrigar, ameaçar ou manipular outra pessoa. Podemos estudar a passagem e conversar sobre uma forma respeitosa de comunicação.",
        )], []
    if decision.intent == "HISTORICAL_RESEARCH":
        return [ContentBlock(
            block_type="HISTORICAL_CONTEXT",
            content="Você está consultando um corpus histórico, distinto das Escrituras canônicas. As fontes históricas de Nag Hammadi e dos Manuscritos do Mar Morto ainda não estão integradas e licenciadas neste aplicativo. Não vou inventar manuscritos, traduções, datações ou citações; nesta versão, use o leitor bíblico para textos já disponibilizados.",
        )], []
    if decision.intent == "TRADITION_COMPARISON":
        return [ContentBlock(
            block_type="INTERPRETATION",
            content="As tradições podem ter cânones e interpretações diferentes. Este primeiro catálogo ainda não inclui os textos e pareceres editoriais necessários para uma comparação denominacional com fontes completas. Não vou declarar equivalência nem superioridade sem as fontes revisadas.",
        )], []
    if decision.intent == "PRAYER_ASSISTANCE":
        return [ContentBlock(
            block_type="PRAYER_SUGGESTION",
            content="Caso faça sentido para você, adapte esta oração: Deus, concede-me sabedoria para agir com responsabilidade, serenidade diante das incertezas e cuidado com as pessoas ao meu redor. Amém. Esta é uma sugestão de reflexão, não uma promessa de resultado.",
        )], []

    parsed = parse_reference(request.content)
    unit = None
    rights = None
    if parsed is not None:
        # Copyright/rights checked before loading source content, including in
        # local mode; production additionally requires editorial/legal approval.
        rights = rights_service.require("AI_QUOTE")
        work_id, chapter, verse = parsed
        unit = next((item for item in units_for_chapter(work_id, chapter) if item.verse == verse), None)

    if unit is None:
        if parsed:
            message = "Não encontrei esse versículo no corpus disponível. Confira o livro, o capítulo e o versículo; não vou reconstruir a passagem."
        elif decision.intent == "SCRIPTURE_READING":
            message = "Para mostrar apenas o texto, indique livro, capítulo e versículo. Exemplo: Gênesis 1:1."
        else:
            message = "Posso organizar um estudo fundamentado em uma fonte disponível. Informe livro, capítulo e versículo (ex.: Gênesis 1:1). Para questões pessoais, não substituo sua consciência nem apoio humano."
        return [ContentBlock(block_type="INTERPRETATION", content=message)], []

    if rights is None or unit.source_id != rights.source_id:
        raise RightsPolicyError(
            "BIBLE_RIGHTS_SOURCE_MISMATCH",
            "A fonte solicitada não corresponde ao registro de direitos.",
        )
    citation = CitationEnvelope(
        citation_id=new_id("cit"),
        corpus_id=unit.corpus_id,
        work_id=unit.work_id,
        text_unit_id=unit.text_unit_id,
        translation_id=unit.translation_id,
        source_id=unit.source_id,
        source_version=unit.source_version,
        rights_record_id=rights.rights_record_id,
        display_label=f"{unit.reference} — Bíblia Livre, versão {unit.source_version}",
    )
    citations.append(citation)
    blocks.append(ContentBlock(block_type="SCRIPTURE_QUOTE", content=unit.text, citations=[citation]))

    if decision.intent == "SCRIPTURE_READING":
        return blocks, citations  # The user's "just the text" constraint wins.
    if decision.intent == "PERSONAL_REFLECTION":
        blocks.append(ContentBlock(
            block_type="AI_REFLECTION",
            content="Como reflexão possível — não como uma ordem de Deus — observe o que a passagem afirma e quais decisões estão sob seu controle. Não seria responsável deduzir dela uma resposta definitiva sobre sua vida.",
        ))
    elif decision.intent == "THEMATIC_STUDY":
        blocks.append(ContentBlock(
            block_type="INTERPRETATION",
            content="Para um estudo temático responsável, compare esta passagem com outras fontes verificadas e examine o contexto de cada uma. A versão determinística ainda não oferece uma síntese acadêmica validada.",
        ))
    else:
        blocks.append(ContentBlock(
            block_type="INTERPRETATION",
            content="Este é o texto da tradução identificada. Para interpretá-lo, considere o capítulo completo, os destinatários e o gênero literário. Nesta versão determinística não há comentário histórico validado para afirmar um sentido exclusivo.",
        ))
    return blocks, citations


def _sse(event: dict[str, object]) -> str:
    return f"event: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


async def _stream(request: ConversationRequest, principal: BiblePrincipal) -> AsyncIterator[str]:
    request_id = request.request_id or new_id("req")
    execution_id = new_id("exe")
    sequence = 0
    terminal_sent = False

    def event(event_type: str, payload: dict[str, object]) -> dict[str, object]:
        nonlocal sequence
        sequence += 1
        return {
            "schema_version": "bible.sse.v1",
            "event_id": new_id("evt"),
            "event_type": event_type,
            "request_id": request_id,
            "execution_id": execution_id,
            "tenant_id": principal.tenant_id,
            "thread_id": request.thread_id,
            "agent_id": "bible_guide",
            "turn_owner": "bible_guide",
            "sequence": sequence,
            "payload": payload,
            "created_at": now().isoformat(),
        }

    try:
        try:
            store.get_thread(principal.tenant_id, principal.user_id, request.thread_id)
        except KeyError:
            store.create_thread(principal.tenant_id, principal.user_id, request.thread_id, "Estudo bíblico")
        # Conversation content is RESTRICTED. Until encrypted conversation
        # storage, consent and retention are implemented, never persist raw
        # user text (including prayers, doubts or potentially sensitive input).
        # The original request remains ephemeral within this execution only.
        user_message = ConversationMessage(
            message_id=new_id("msg"),
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            thread_id=request.thread_id,
            role="user",
            blocks=[ContentBlock(
                block_type="APPLICATION",
                content="[Conteúdo da pergunta não armazenado: histórico privado pendente de criptografia.]",
            )],
        )
        store.add_message(user_message)
        decision = resolve_intent(request.content, request.mode)
        yield _sse(event("meta", {
            "mode": decision.mode,
            "primary_intent": decision.intent,
            "intent_confidence": decision.confidence,
            "policy_ids": list(decision.policy_ids),
            "canon_profile": "ECUMENICAL_NEUTRAL",
            "behavior_version": "bible.guide.v1-deterministic",
            "raw_user_content_persisted": False,
            "done_observed": False,
            "transport": "sse",
        }))
        blocks, citations = _assistant_blocks(request, decision)
        if decision.intent not in {
            "SCRIPTURE_READING", "CRISIS_SUPPORT", "SPIRITUAL_GUILT_SUPPORT",
            "PROFESSIONAL_BOUNDARY", "PERSONAL_PROPHECY", "COERCION_GUARD",
        }:
            preference_note = optional_guide_context(get_consenting_preferences(principal))
            if preference_note:
                blocks.append(ContentBlock(block_type="INTERPRETATION", content=preference_note))
        for citation in citations:
            yield _sse(event("citation", {"citation": citation.model_dump()}))
        await asyncio.sleep(0)
        assistant = ConversationMessage(message_id=new_id("msg"), tenant_id=principal.tenant_id, user_id=principal.user_id, thread_id=request.thread_id, role="assistant", blocks=blocks)
        store.add_message(assistant)
        for block in blocks:
            yield _sse(event("content.delta", {"block_type": block.block_type, "delta": block.content, "citations": [c.model_dump() for c in block.citations]}))
        yield _sse(event("content.final", {"message_id": assistant.message_id, "blocks": [block.model_dump() for block in blocks]}))
        yield _sse(event("done", {"outcome": "success", "done_observed": True, "terminal_source": "wire", "message_id": assistant.message_id}))
        terminal_sent = True
    except asyncio.CancelledError:
        yield _sse(event("cancelled", {"reason": "client_disconnected"}))
        yield _sse(event("done", {"outcome": "cancelled", "done_observed": True, "terminal_source": "wire"}))
        terminal_sent = True
    except RightsPolicyError as exc:
        yield _sse(event("error", {"code": exc.code, "message": exc.message}))
        yield _sse(event("done", {"outcome": "error", "done_observed": True, "terminal_source": "wire"}))
        terminal_sent = True
    except Exception:
        yield _sse(event("error", {"code": "BIBLE_STREAM_FAILED", "message": "Não foi possível concluir o estudo."}))
        yield _sse(event("done", {"outcome": "error", "done_observed": True, "terminal_source": "wire"}))
        terminal_sent = True
    finally:
        if not terminal_sent:
            yield _sse(event("done", {"outcome": "error", "done_observed": True, "terminal_source": "wire"}))


@router.post("/{thread_id}/stream")
async def stream_conversation(thread_id: str, request: ConversationRequest, principal: BiblePrincipal = Depends(get_principal)) -> StreamingResponse:
    request.thread_id = thread_id
    return StreamingResponse(_stream(request, principal), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@router.get("/{thread_id}/messages")
def list_messages(thread_id: str, principal: BiblePrincipal = Depends(get_principal)) -> dict[str, object]:
    return {"thread_id": thread_id, "messages": [message.model_dump() for message in store.list_messages(principal.tenant_id, principal.user_id, thread_id)]}
