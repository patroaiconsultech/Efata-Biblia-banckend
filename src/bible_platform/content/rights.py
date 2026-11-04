from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from bible_platform.config import Settings, get_settings
from bible_platform.content.catalog import local_rights_record
from bible_platform.domain_models import RightsRecord
from bible_platform.infrastructure.store import store

RightsOperation = Literal[
    "READ",
    "SEARCH",
    "QUOTE",
    "AI_QUOTE",
    "RAG",
    "IMAGE",
]

_RIGHTS_QUERY = text(
    """
    SELECT
        rights_record_id,
        source_id,
        commercial_use,
        redistribution,
        modification,
        ai_processing,
        rag_ingestion,
        attribution_required,
        legal_review
    FROM bible_rights_records
    WHERE rights_record_id = :rights_record_id
    """
)


class RightsPolicyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RightsRepositoryUnavailable(RightsPolicyError):
    pass


class RightsAccessDenied(RightsPolicyError):
    pass


class RightsService:
    """Single runtime authority for content rights.

    In staging/production the database/Knowledge Plane is authoritative.
    A static fallback exists only for local development with no configured DB.
    If a DB is configured but its rights registry is unavailable, fail closed
    rather than silently falling back to a competing source of truth.
    """

    def __init__(
        self,
        *,
        engine_provider: Callable[[], Engine | None] | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
        local_fallback_provider: Callable[[], RightsRecord] = local_rights_record,
    ) -> None:
        self._engine_provider = engine_provider or (lambda: store.engine)
        self._settings_provider = settings_provider
        self._local_fallback_provider = local_fallback_provider

    def get(self, rights_record_id: str = "rights_bible_livre") -> RightsRecord:
        settings = self._settings_provider()
        engine = self._engine_provider()

        if engine is None:
            if settings.production:
                raise RightsRepositoryUnavailable(
                    "BIBLE_RIGHTS_REGISTRY_UNAVAILABLE",
                    "O registro de direitos não está disponível neste ambiente.",
                )
            return self._local_fallback_provider()

        try:
            with engine.connect() as connection:
                row = connection.execute(
                    _RIGHTS_QUERY, {"rights_record_id": rights_record_id}
                ).mappings().first()
        except SQLAlchemyError as exc:
            raise RightsRepositoryUnavailable(
                "BIBLE_RIGHTS_REGISTRY_UNAVAILABLE",
                "Não foi possível consultar o registro de direitos.",
            ) from exc

        if row is None:
            raise RightsRepositoryUnavailable(
                "BIBLE_RIGHTS_RECORD_NOT_FOUND",
                "O registro de direitos requerido não foi encontrado.",
            )

        return RightsRecord(
            rights_record_id=row["rights_record_id"],
            source_id=row["source_id"],
            commercial_use=bool(row["commercial_use"]),
            redistribution=bool(row["redistribution"]),
            modification=bool(row["modification"]),
            ai_processing=bool(row["ai_processing"]),
            rag_ingestion=bool(row["rag_ingestion"]),
            attribution_required=bool(row["attribution_required"]),
            legal_review=row["legal_review"],
        )

    def require(
        self,
        operation: RightsOperation,
        *,
        rights_record_id: str = "rights_bible_livre",
        source_id: str | None = None,
    ) -> RightsRecord:
        settings = self._settings_provider()
        record = self.get(rights_record_id)

        if source_id is not None and record.source_id != source_id:
            raise RightsAccessDenied(
                "BIBLE_RIGHTS_SOURCE_MISMATCH",
                "A fonte solicitada não corresponde ao registro de direitos.",
            )

        # Local development may use the documented static corpus while legal
        # review remains pending. Staging/production must always fail closed.
        if not settings.production:
            return record

        if record.legal_review != "approved":
            raise RightsAccessDenied(
                "BIBLE_RIGHTS_REVIEW_REQUIRED",
                "O corpus aguarda revisão de direitos antes da publicação.",
            )

        required_flags: dict[RightsOperation, tuple[str, ...]] = {
            "READ": ("redistribution",),
            "SEARCH": ("redistribution",),
            "QUOTE": ("redistribution",),
            "AI_QUOTE": ("redistribution", "ai_processing"),
            "RAG": ("rag_ingestion", "ai_processing"),
            "IMAGE": ("image_display",),
        }
        denied = [
            flag for flag in required_flags[operation]
            if not bool(getattr(record, flag))
        ]
        if denied:
            raise RightsAccessDenied(
                "BIBLE_RIGHTS_OPERATION_NOT_ALLOWED",
                f"A licença não autoriza a operação {operation}.",
            )
        return record

    def status(self, rights_record_id: str = "rights_bible_livre") -> dict[str, object]:
        try:
            record = self.get(rights_record_id)
            return {
                "available": True,
                "legal_review": record.legal_review,
                "publication_ready": record.usable_for_publication,
                "rights_record_id": record.rights_record_id,
                "source_id": record.source_id,
                "error_code": None,
            }
        except RightsPolicyError as exc:
            return {
                "available": False,
                "legal_review": None,
                "publication_ready": False,
                "rights_record_id": rights_record_id,
                "source_id": None,
                "error_code": exc.code,
            }


rights_service = RightsService()
