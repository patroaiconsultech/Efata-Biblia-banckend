"""Behavioral Runtime v1 regression suite: bounded deterministic responses."""
import json

import pytest
from fastapi.testclient import TestClient

from bible_platform.behavior.intent import parse_reference, resolve_intent
from bible_platform.domain_models import ConversationRequest
from bible_platform.api.routes.chat import _assistant_blocks


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    monkeypatch.setenv("BIBLE_AUTH_MODE", "demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_TENANTS", "tenant-demo")
    monkeypatch.setenv("BIBLE_DEMO_ALLOWED_USERS", "user-demo")
    from bible_platform.config import get_settings
    get_settings.cache_clear()
    from bible_platform.main import app
    return TestClient(app)


def req(text, mode="SCRIPTURE_EXPLANATION"):
    return ConversationRequest(thread_id="guide-rc04", content=text, mode=mode)


@pytest.mark.parametrize("text,work", [
    ("Explique Gênesis 1:1", "work_gen"),
    ("Gn.1.1", "work_gen"),
    ("Mostre João 1:1", "work_joao"),
    ("Leia 1 João 1:1", "work_1joao"),
    ("2 João 1:1", "work_2joao"),
    ("Salmo 23:1", "work_sal"),
])
def test_reference_resolution_is_book_specific(text, work):
    result = parse_reference(text)
    assert result is not None
    assert result[0] == work


@pytest.mark.parametrize("text", ["Gênesis 0:1", "Gn 1", "João Batista", "Gênesis 1:0"])
def test_reference_must_be_valid_shape(text):
    assert parse_reference(text) is None


@pytest.mark.parametrize("text,intent", [
    ("Leia Gênesis 1:1, apenas o texto", "SCRIPTURE_READING"),
    ("Faça uma oração pela minha família", "PRAYER_ASSISTANCE"),
    ("Quero estudar Nag Hammadi", "HISTORICAL_RESEARCH"),
    ("Qual a diferença entre Bíblia católica e protestante?", "TRADITION_COMPARISON"),
    ("Profetize sobre meu futuro", "PERSONAL_PROPHECY"),
    ("Deus quer que eu largue meu emprego?", "PERSONAL_PROPHECY"),
    ("Deus mandou minha esposa obedecer", "COERCION_GUARD"),
    ("Posso parar meu tratamento e só orar?", "PROFESSIONAL_BOUNDARY"),
    ("Quero me matar", "CRISIS_SUPPORT"),
    ("Deus está me castigando?", "SPIRITUAL_GUILT_SUPPORT"),
    ("Estou perdendo a fé", "FAITH_DOUBT"),
])
def test_governed_intent(text, intent):
    assert resolve_intent(text, "SCRIPTURE_EXPLANATION").intent == intent


def test_no_prayer_preference_overrides_selected_mode():
    decision = resolve_intent("Não quero oração", "PRAYER_ASSISTANCE")
    assert decision.intent != "PRAYER_ASSISTANCE"


def test_read_only_passage_has_no_interpretation_or_prayer():
    request = req("Leia Gênesis 1:1, apenas o texto")
    decision = resolve_intent(request.content, request.mode)
    blocks, cites = _assistant_blocks(request, decision)
    assert [x.block_type for x in blocks] == ["SCRIPTURE_QUOTE"]
    assert len(cites) == 1
    assert cites[0].source_id == "bible-livre"


def test_explanation_identifies_source_and_does_not_claim_exclusive_interpretation():
    request = req("Explique Gênesis 1:1")
    blocks, cites = _assistant_blocks(request, resolve_intent(request.content, request.mode))
    assert [x.block_type for x in blocks] == ["SCRIPTURE_QUOTE", "INTERPRETATION"]
    assert len(cites) == 1
    assert "não há comentário" in blocks[1].content
    assert "não há" in blocks[1].content
    assert "sentido exclusivo" in blocks[1].content


def test_prayer_only_when_requested():
    request = req("Faça uma oração pela minha família")
    blocks, cites = _assistant_blocks(request, resolve_intent(request.content, request.mode))
    assert [x.block_type for x in blocks] == ["PRAYER_SUGGESTION"]
    assert cites == []
    assert "não uma promessa" in blocks[0].content


def test_historical_corpus_does_not_claim_unavailable_sources():
    request = req("Explique os Manuscritos do Mar Morto")
    blocks, cites = _assistant_blocks(request, resolve_intent(request.content, request.mode))
    assert [x.block_type for x in blocks] == ["HISTORICAL_CONTEXT"]
    assert cites == []
    assert "ainda não estão integradas" in blocks[0].content


def test_safety_overrides_quote_even_with_reference():
    request = req("Deus quer que eu largue meu emprego? Gênesis 1:1")
    decision = resolve_intent(request.content, request.mode)
    blocks, cites = _assistant_blocks(request, decision)
    assert [x.block_type for x in blocks] == ["SAFETY_NOTICE"]
    assert cites == []


def test_stream_contains_intent_and_terminal_and_owner(client):
    res = client.post("/api/conversations/behavior-stream/stream",
        headers={"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-demo", "X-Role": "member"},
        json={"thread_id": "ignored", "content": "Leia Gênesis 1:1, apenas o texto", "mode": "SCRIPTURE_EXPLANATION"})
    assert res.status_code == 200
    assert "event: done" in res.text
    assert "event: citation" in res.text
    data = [json.loads(line.removeprefix("data: ")) for line in res.text.splitlines() if line.startswith("data: ")]
    assert data[0]["event_type"] == "meta"
    assert data[0]["payload"]["primary_intent"] == "SCRIPTURE_READING"
    assert data[0]["payload"]["behavior_version"] == "bible.guide.v1-deterministic"
    assert data[-1]["event_type"] == "done"
    assert data[-1]["payload"]["outcome"] == "success"
    assert all(evt["agent_id"] == "bible_guide" and evt["turn_owner"] == "bible_guide" for evt in data)
    finals = [evt for evt in data if evt["event_type"] == "content.final"]
    assert len(finals) == 1
    assert [b["block_type"] for b in finals[0]["payload"]["blocks"]] == ["SCRIPTURE_QUOTE"]


def test_immediate_safety_no_scripture_or_unfounded_divine_cause():
    request = req("Quero me matar")
    blocks, cites = _assistant_blocks(request, resolve_intent(request.content, request.mode))
    assert [block.block_type for block in blocks] == ["SAFETY_NOTICE"]
    assert "serviço local de emergência" in blocks[0].content
    assert cites == []


def test_spiritual_guilt_is_not_interpreted_as_divine_punishment():
    request = req("Deus está me castigando?")
    blocks, cites = _assistant_blocks(request, resolve_intent(request.content, request.mode))
    assert len(blocks) == 1
    assert "não seria responsável" in blocks[0].content.lower()
    assert cites == []


def test_conversation_input_not_persisted_in_plaintext(client):
    headers = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-demo", "X-Role": "member"}
    secret = "informação pessoal que não deve ser gravada TEST-PRIVATE-74296"
    thread = "privacy-test-rc04"
    res = client.post(f"/api/conversations/{thread}/stream", headers=headers,
        json={"thread_id": "ignored", "content": f"Estou sofrendo com {secret}", "mode": "PERSONAL_REFLECTION"})
    assert res.status_code == 200
    assert "event: done" in res.text
    history = client.get(f"/api/conversations/{thread}/messages", headers=headers)
    assert history.status_code == 200
    serialized = json.dumps(history.json(), ensure_ascii=False)
    assert secret not in serialized
    assert "Conteúdo da pergunta não armazenado" in serialized
