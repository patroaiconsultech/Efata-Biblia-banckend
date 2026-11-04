from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


Role = Literal["member", "admin", "editor", "researcher"]
ContentBlockType = Literal[
    "SCRIPTURE_QUOTE",
    "HISTORICAL_TEXT",
    "HISTORICAL_CONTEXT",
    "INTERPRETATION",
    "AI_REFLECTION",
    "APPLICATION",
    "PRAYER_SUGGESTION",
    "SAFETY_NOTICE",
]


class BiblePrincipal(BaseModel):
    model_config = ConfigDict(frozen=True)
    user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    role: Role = "member"
    scopes: tuple[str, ...] = ()
    issuer: str = "demo"
    subject: str = "demo"
    auth_method: Literal["demo", "oidc", "native_session"] = "demo"


class Corpus(BaseModel):
    corpus_id: str
    name: str
    description: str
    status: Literal["active", "planned"] = "active"


class CanonProfile(BaseModel):
    profile_id: str
    name: str
    description: str


class Work(BaseModel):
    work_id: str
    corpus_id: str
    title: str
    slug: str
    testament: Literal["old", "new", "historical", "other"]
    book_order: int
    canonical_status: dict[str, str]


class Translation(BaseModel):
    translation_id: str
    name: str
    language: str
    version: str
    source_id: str
    rights_record_id: str
    status: Literal["approved", "pending", "suspended"]


class TextUnit(BaseModel):
    text_unit_id: str
    work_id: str
    translation_id: str
    chapter: int = Field(ge=1)
    verse: int = Field(ge=1)
    reference: str
    text: str
    source_id: str
    source_version: str
    corpus_id: str = "canonical_scripture"
    editorial_status: Literal["PUBLISHED", "DRAFT", "SUSPENDED"] = "PUBLISHED"


class SourceRegistry(BaseModel):
    source_id: str
    title: str
    url: str
    version: str
    retrieved_at: datetime
    attribution: str


class RightsRecord(BaseModel):
    rights_record_id: str
    source_id: str
    commercial_use: bool
    redistribution: bool
    modification: bool
    ai_processing: bool
    rag_ingestion: bool
    image_display: bool = False
    attribution_required: bool = True
    territory: str = "global"
    legal_review: Literal["pending", "approved", "rejected"] = "pending"

    @property
    def usable_for_publication(self) -> bool:
        return self.redistribution and self.legal_review == "approved"


class ProgressRecord(BaseModel):
    tenant_id: str
    user_id: str
    text_unit_id: str
    completed: bool = False
    updated_at: datetime = Field(default_factory=now)


class PersonalEntry(BaseModel):
    entry_id: str
    tenant_id: str
    user_id: str
    kind: Literal["journal", "prayer", "reflection"]
    ciphertext: str
    key_version: str
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class CitationEnvelope(BaseModel):
    citation_id: str
    corpus_id: str
    work_id: str
    text_unit_id: str
    translation_id: str | None = None
    source_id: str
    source_version: str
    rights_record_id: str
    display_label: str


class ContentBlock(BaseModel):
    block_type: ContentBlockType
    content: str
    citations: list[CitationEnvelope] = []


class ConversationMessage(BaseModel):
    message_id: str
    tenant_id: str
    user_id: str
    thread_id: str
    role: Literal["user", "assistant"]
    blocks: list[ContentBlock]
    created_at: datetime = Field(default_factory=now)


class BibleResponseEnvelope(BaseModel):
    message_id: str
    request_id: str
    execution_id: str
    thread_id: str
    tenant_id: str
    agent_id: str
    turn_owner: str
    route_family: str
    blocks: list[ContentBlock]
    status: Literal["success", "partial", "error", "cancelled"]
    error: dict[str, Any] | None = None
    transport: Literal["http_json", "sse"] = "http_json"
    terminal_source: Literal["envelope", "wire"] = "envelope"
    created_at: datetime = Field(default_factory=now)

    @model_validator(mode="after")
    def validate_owner(self) -> "BibleResponseEnvelope":
        if self.agent_id != self.turn_owner:
            raise ValueError("BIBLE_OWNERSHIP_DIVERGENCE")
        return self


class BibleSSEEvent(BaseModel):
    schema_version: Literal["bible.sse.v1"] = "bible.sse.v1"
    event_id: str
    event_type: Literal["meta", "citation", "content.delta", "content.final", "usage", "error", "cancelled", "done"]
    request_id: str
    execution_id: str
    tenant_id: str
    thread_id: str
    agent_id: str
    turn_owner: str
    sequence: int
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=now)


class JournalCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    kind: Literal["journal", "prayer", "reflection"] = "journal"


class ProgressUpdate(BaseModel):
    completed: bool


class HighlightCreate(BaseModel):
    text_unit_id: str = Field(min_length=1, max_length=160)
    note: str = Field(default="", max_length=2_000)
    color: Literal["gold", "teal", "blue", "rose"] = "gold"


class ConversationRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=20_000)
    mode: Literal["SCRIPTURE_EXPLANATION", "THEMATIC_STUDY", "PERSONAL_REFLECTION", "PRAYER_ASSISTANCE"] = "SCRIPTURE_EXPLANATION"
    request_id: str | None = None
