from __future__ import annotations

from sqlalchemy import create_engine, text

from bible_platform.config import Settings
from bible_platform.content.catalog import local_rights_record
from bible_platform.content.rights import (
    RightsAccessDenied,
    RightsRepositoryUnavailable,
    RightsService,
)


def _create_rights_table(engine, *, legal_review: str = "approved", ai_processing: bool = True) -> None:
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE bible_rights_records (
                rights_record_id VARCHAR(120) PRIMARY KEY,
                source_id VARCHAR(120) NOT NULL,
                commercial_use BOOLEAN NOT NULL,
                redistribution BOOLEAN NOT NULL,
                modification BOOLEAN NOT NULL,
                ai_processing BOOLEAN NOT NULL,
                rag_ingestion BOOLEAN NOT NULL,
                attribution_required BOOLEAN NOT NULL,
                legal_review VARCHAR(30) NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO bible_rights_records(
                rights_record_id, source_id, commercial_use, redistribution,
                modification, ai_processing, rag_ingestion,
                attribution_required, legal_review
            ) VALUES (
                'rights_bible_livre', 'bible-livre', true, true,
                true, :ai_processing, true, true, :legal_review
            )
        """), {"ai_processing": ai_processing, "legal_review": legal_review})


def test_database_is_authoritative_when_configured(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rights.db'}")
    _create_rights_table(engine, legal_review="approved")
    settings = Settings(environment="production")

    fallback = local_rights_record()
    assert fallback.legal_review == "pending"

    service = RightsService(
        engine_provider=lambda: engine,
        settings_provider=lambda: settings,
        local_fallback_provider=lambda: fallback,
    )
    record = service.get()
    assert record.legal_review == "approved"
    assert service.require("READ").rights_record_id == "rights_bible_livre"


def test_configured_database_never_falls_back_when_registry_missing(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'missing-rights.db'}")
    settings = Settings(environment="production")
    service = RightsService(
        engine_provider=lambda: engine,
        settings_provider=lambda: settings,
        local_fallback_provider=local_rights_record,
    )

    try:
        service.get()
    except RightsRepositoryUnavailable as exc:
        assert exc.code == "BIBLE_RIGHTS_REGISTRY_UNAVAILABLE"
    else:
        raise AssertionError("configured DB must fail closed if rights registry is unavailable")


def test_pending_legal_review_blocks_publication_operations(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pending.db'}")
    _create_rights_table(engine, legal_review="pending")
    settings = Settings(environment="production")
    service = RightsService(
        engine_provider=lambda: engine,
        settings_provider=lambda: settings,
    )

    for operation in ("READ", "SEARCH", "QUOTE", "AI_QUOTE"):
        try:
            service.require(operation)
        except RightsAccessDenied as exc:
            assert exc.code == "BIBLE_RIGHTS_REVIEW_REQUIRED"
        else:
            raise AssertionError(f"{operation} should be blocked while legal review is pending")


def test_ai_quote_requires_ai_processing_right(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'no-ai.db'}")
    _create_rights_table(engine, legal_review="approved", ai_processing=False)
    settings = Settings(environment="production")
    service = RightsService(
        engine_provider=lambda: engine,
        settings_provider=lambda: settings,
    )

    assert service.require("READ").legal_review == "approved"
    try:
        service.require("AI_QUOTE")
    except RightsAccessDenied as exc:
        assert exc.code == "BIBLE_RIGHTS_OPERATION_NOT_ALLOWED"
    else:
        raise AssertionError("AI_QUOTE must require ai_processing")


def test_local_without_database_uses_development_fallback_only():
    settings = Settings(environment="local")
    service = RightsService(
        engine_provider=lambda: None,
        settings_provider=lambda: settings,
        local_fallback_provider=local_rights_record,
    )
    assert service.get().legal_review == "pending"
    assert service.require("READ").rights_record_id == "rights_bible_livre"


def test_production_without_database_has_no_static_fallback():
    settings = Settings(environment="production")
    service = RightsService(
        engine_provider=lambda: None,
        settings_provider=lambda: settings,
        local_fallback_provider=local_rights_record,
    )
    try:
        service.get()
    except RightsRepositoryUnavailable as exc:
        assert exc.code == "BIBLE_RIGHTS_REGISTRY_UNAVAILABLE"
    else:
        raise AssertionError("production must not use static rights fallback")
