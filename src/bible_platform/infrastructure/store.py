from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, Text, and_, create_engine, delete, insert, inspect, select, text, update

from bible_platform.config import get_settings
from bible_platform.domain_models import ConversationMessage, PersonalEntry, ProgressRecord
from bible_platform.security_crypto import EncryptedValue, decrypt_restricted, encrypt_restricted

from bible_platform.infrastructure.schema import (
    metadata, threads_table, messages_table, progress_table,
    bookmarks_table, highlights_table, personal_table,
)

def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _encrypt_highlight_note(note: str) -> str:
    return f"enc:v1:{encrypt_restricted(note).ciphertext}" if note else ""


def _decrypt_highlight_note(note: str) -> str:
    if not note:
        return ""
    if note.startswith("enc:v1:"):
        return decrypt_restricted(EncryptedValue(ciphertext=note.removeprefix("enc:v1:"), key_version="v1"))
    # Legacy notes may exist in local development; never return plaintext legacy
    # rows in staging/production without an explicit, reviewed migration.
    if get_settings().environment in {"staging", "production"}:
        from bible_platform.security_crypto import EncryptionError
        raise EncryptionError("BIBLE_HIGHLIGHT_LEGACY_NOTE_REQUIRES_MIGRATION")
    return note


def _highlight_for_response(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "note": _decrypt_highlight_note(row["note"])}


def _set_rls_context(connection, tenant_id: str, user_id: str) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})
        connection.execute(text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": user_id})


class Store:
    def __init__(self) -> None:
        settings = get_settings()
        self.backend_name = "memory"
        self.engine = None
        if settings.database_url:
            url = settings.database_url
            if url.startswith("postgres://"):
                url = "postgresql+psycopg://" + url.removeprefix("postgres://")
            elif url.startswith("postgresql://"):
                url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
            self.engine = create_engine(url, pool_pre_ping=True)
            self.backend_name = "sqlalchemy"
            if not settings.production or __import__("os").getenv("BIBLE_AUTO_CREATE_SCHEMA", "false").lower() == "true":
                metadata.create_all(self.engine)
        self._lock = RLock()
        self._threads: dict[tuple[str, str], dict[str, Any]] = {}
        self._messages: dict[tuple[str, str], list[ConversationMessage]] = defaultdict(list)
        self._progress: dict[tuple[str, str, str], ProgressRecord] = {}
        self._personal: dict[tuple[str, str], PersonalEntry] = {}
        self._bookmarks: set[tuple[str, str, str]] = set()
        self._highlights: dict[tuple[str, str], dict[str, Any]] = {}
        self._faith_preferences: dict[tuple[str, str], dict[str, Any]] = {}

    def ping(self) -> bool:
        if self.engine is None:
            return not get_settings().production
        with self.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return True

    def schema_ready(self) -> bool:
        if self.engine is None:
            return not get_settings().production
        required = {"bible_threads", "bible_messages", "bible_progress", "bible_personal_entries", "bible_bookmarks", "bible_highlights", "bible_faith_preferences"}
        return required.issubset(set(inspect(self.engine).get_table_names()))

    def create_thread(self, tenant_id: str, user_id: str, thread_id: str, title: str) -> dict[str, Any]:
        row = {"tenant_id": tenant_id, "thread_id": thread_id, "user_id": user_id, "title": title, "created_at": _utc()}
        if self.engine is not None:
            with self.engine.begin() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                conn.execute(insert(threads_table).values(**row))
        with self._lock:
            self._threads[(tenant_id, thread_id)] = row
        return row

    def list_threads(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        if self.engine is not None:
            with self.engine.connect() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                rows = conn.execute(select(threads_table).where(and_(threads_table.c.tenant_id == tenant_id, threads_table.c.user_id == user_id)).order_by(threads_table.c.created_at.desc())).mappings().all()
                return [dict(row) for row in rows]
        with self._lock:
            return sorted((row for (tenant, _), row in self._threads.items() if tenant == tenant_id and row["user_id"] == user_id), key=lambda row: row["created_at"], reverse=True)

    def get_thread(self, tenant_id: str, user_id: str, thread_id: str) -> dict[str, Any]:
        if self.engine is not None:
            with self.engine.connect() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                row = conn.execute(select(threads_table).where(and_(threads_table.c.tenant_id == tenant_id, threads_table.c.user_id == user_id, threads_table.c.thread_id == thread_id))).mappings().first()
                if row:
                    return dict(row)
        with self._lock:
            row = self._threads.get((tenant_id, thread_id))
            if row and row["user_id"] == user_id:
                return row
        raise KeyError("BIBLE_THREAD_NOT_FOUND")

    def add_message(self, message: ConversationMessage) -> None:
        payload = message.model_dump_json()
        if self.engine is not None:
            with self.engine.begin() as conn:
                _set_rls_context(conn, message.tenant_id, message.user_id)
                conn.execute(insert(messages_table).values(tenant_id=message.tenant_id, message_id=message.message_id, thread_id=message.thread_id, user_id=message.user_id, role=message.role, blocks_json=payload, created_at=message.created_at))
        with self._lock:
            self._messages[(message.tenant_id, message.thread_id)].append(message)

    def list_messages(self, tenant_id: str, user_id: str, thread_id: str) -> list[ConversationMessage]:
        self.get_thread(tenant_id, user_id, thread_id)
        if self.engine is not None:
            with self.engine.connect() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                rows = conn.execute(select(messages_table).where(and_(messages_table.c.tenant_id == tenant_id, messages_table.c.thread_id == thread_id, messages_table.c.user_id == user_id)).order_by(messages_table.c.created_at.asc())).mappings().all()
                return [ConversationMessage.model_validate_json(row["blocks_json"]) for row in rows]
        with self._lock:
            return list(self._messages[(tenant_id, thread_id)])

    def upsert_progress(self, progress: ProgressRecord) -> ProgressRecord:
        if self.engine is not None:
            values = progress.model_dump()
            with self.engine.begin() as conn:
                _set_rls_context(conn, progress.tenant_id, progress.user_id)
                existing = conn.execute(select(progress_table).where(and_(progress_table.c.tenant_id == progress.tenant_id, progress_table.c.user_id == progress.user_id, progress_table.c.text_unit_id == progress.text_unit_id))).first()
                if existing:
                    conn.execute(update(progress_table).where(and_(progress_table.c.tenant_id == progress.tenant_id, progress_table.c.user_id == progress.user_id, progress_table.c.text_unit_id == progress.text_unit_id)).values(completed=progress.completed, updated_at=progress.updated_at))
                else:
                    conn.execute(insert(progress_table).values(**values))
        with self._lock:
            self._progress[(progress.tenant_id, progress.user_id, progress.text_unit_id)] = progress
        return progress

    def list_progress(self, tenant_id: str, user_id: str) -> list[ProgressRecord]:
        if self.engine is not None:
            with self.engine.connect() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                rows = conn.execute(select(progress_table).where(and_(progress_table.c.tenant_id == tenant_id, progress_table.c.user_id == user_id))).mappings().all()
                return [ProgressRecord.model_validate(row) for row in rows]
        with self._lock:
            return [item for (tenant, owner, _), item in self._progress.items() if tenant == tenant_id and owner == user_id]

    def upsert_bookmark(self, tenant_id: str, user_id: str, text_unit_id: str, active: bool) -> None:
        key = (tenant_id, user_id, text_unit_id)
        if self.engine is not None:
            with self.engine.begin() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                if active:
                    existing = conn.execute(select(bookmarks_table).where(and_(bookmarks_table.c.tenant_id == tenant_id, bookmarks_table.c.user_id == user_id, bookmarks_table.c.text_unit_id == text_unit_id))).first()
                    if not existing:
                        conn.execute(insert(bookmarks_table).values(tenant_id=tenant_id, user_id=user_id, text_unit_id=text_unit_id, created_at=_utc()))
                else:
                    conn.execute(delete(bookmarks_table).where(and_(bookmarks_table.c.tenant_id == tenant_id, bookmarks_table.c.user_id == user_id, bookmarks_table.c.text_unit_id == text_unit_id)))
        with self._lock:
            if active:
                self._bookmarks.add(key)
            else:
                self._bookmarks.discard(key)

    def list_bookmarks(self, tenant_id: str, user_id: str) -> list[str]:
        if self.engine is not None:
            with self.engine.connect() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                rows = conn.execute(select(bookmarks_table.c.text_unit_id).where(and_(bookmarks_table.c.tenant_id == tenant_id, bookmarks_table.c.user_id == user_id))).all()
                return [row[0] for row in rows]
        with self._lock:
            return [text_unit_id for tenant, owner, text_unit_id in self._bookmarks if tenant == tenant_id and owner == user_id]

    def add_highlight(self, tenant_id: str, user_id: str, highlight_id: str, text_unit_id: str, note: str, color: str) -> dict[str, Any]:
        row = {
            "tenant_id": tenant_id, "highlight_id": highlight_id, "user_id": user_id,
            "text_unit_id": text_unit_id, "note": _encrypt_highlight_note(note),
            "color": color, "created_at": _utc(),
        }
        if self.engine is not None:
            with self.engine.begin() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                conn.execute(insert(highlights_table).values(**row))
        with self._lock:
            self._highlights[(tenant_id, highlight_id)] = row
        return _highlight_for_response(row)

    def list_highlights(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        if self.engine is not None:
            with self.engine.connect() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                rows = conn.execute(select(highlights_table).where(
                    and_(highlights_table.c.tenant_id == tenant_id, highlights_table.c.user_id == user_id)
                )).mappings().all()
                return [_highlight_for_response(dict(row)) for row in rows]
        with self._lock:
            return [_highlight_for_response(row) for (tenant, _), row in self._highlights.items()
                    if tenant == tenant_id and row["user_id"] == user_id]

    def delete_highlight(self, tenant_id: str, user_id: str, highlight_id: str) -> None:
        if self.engine is not None:
            with self.engine.begin() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                result = conn.execute(delete(highlights_table).where(and_(highlights_table.c.tenant_id == tenant_id, highlights_table.c.user_id == user_id, highlights_table.c.highlight_id == highlight_id)))
                if result.rowcount == 0:
                    raise KeyError("BIBLE_HIGHLIGHT_NOT_FOUND")
            with self._lock:
                self._highlights.pop((tenant_id, highlight_id), None)
            return  # SQL is authoritative after restart.
        with self._lock:
            if (tenant_id, highlight_id) not in self._highlights or self._highlights[(tenant_id, highlight_id)]["user_id"] != user_id:
                raise KeyError("BIBLE_HIGHLIGHT_NOT_FOUND")
            del self._highlights[(tenant_id, highlight_id)]

    def create_personal(self, tenant_id: str, user_id: str, entry_id: str, kind: str, plaintext: str) -> PersonalEntry:
        encrypted = encrypt_restricted(plaintext)
        entry = PersonalEntry(entry_id=entry_id, tenant_id=tenant_id, user_id=user_id, kind=kind, ciphertext=encrypted.ciphertext, key_version=encrypted.key_version)
        if self.engine is not None:
            with self.engine.begin() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                conn.execute(insert(personal_table).values(**entry.model_dump()))
        with self._lock:
            self._personal[(tenant_id, entry_id)] = entry
        return entry

    def list_personal(self, tenant_id: str, user_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        entries: list[PersonalEntry]
        if self.engine is not None:
            with self.engine.connect() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                clauses = [personal_table.c.tenant_id == tenant_id, personal_table.c.user_id == user_id]
                if kind:
                    clauses.append(personal_table.c.kind == kind)
                rows = conn.execute(select(personal_table).where(and_(*clauses)).order_by(personal_table.c.created_at.desc())).mappings().all()
                entries = [PersonalEntry.model_validate(row) for row in rows]
        else:
            with self._lock:
                entries = [entry for (tenant, _), entry in self._personal.items() if tenant == tenant_id and entry.user_id == user_id and (kind is None or entry.kind == kind)]
        return [{"entry_id": entry.entry_id, "kind": entry.kind, "content": decrypt_restricted(EncryptedValue(entry.ciphertext, entry.key_version)), "created_at": entry.created_at, "updated_at": entry.updated_at} for entry in entries]

    def delete_personal(self, tenant_id: str, user_id: str, entry_id: str) -> None:
        if self.engine is not None:
            with self.engine.begin() as conn:
                _set_rls_context(conn, tenant_id, user_id)
                result = conn.execute(delete(personal_table).where(and_(personal_table.c.tenant_id == tenant_id, personal_table.c.user_id == user_id, personal_table.c.entry_id == entry_id)))
                if result.rowcount == 0:
                    raise KeyError("BIBLE_PERSONAL_ENTRY_NOT_FOUND")
            with self._lock:
                self._personal.pop((tenant_id, entry_id), None)
            return  # SQL is authoritative after restart.
        with self._lock:
            if (tenant_id, entry_id) not in self._personal or self._personal[(tenant_id, entry_id)].user_id != user_id:
                raise KeyError("BIBLE_PERSONAL_ENTRY_NOT_FOUND")
            del self._personal[(tenant_id, entry_id)]


store = Store()
