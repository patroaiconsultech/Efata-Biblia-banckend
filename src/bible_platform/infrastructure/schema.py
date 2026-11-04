from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, Text

metadata = MetaData()
threads_table = Table(
    "bible_threads", metadata,
    Column("tenant_id", String(120), primary_key=True),
    Column("thread_id", String(120), primary_key=True),
    Column("user_id", String(120), nullable=False),
    Column("title", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
messages_table = Table(
    "bible_messages", metadata,
    Column("tenant_id", String(120), primary_key=True),
    Column("message_id", String(120), primary_key=True),
    Column("thread_id", String(120), nullable=False),
    Column("user_id", String(120), nullable=False),
    Column("role", String(20), nullable=False),
    Column("blocks_json", Text(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
progress_table = Table(
    "bible_progress", metadata,
    Column("tenant_id", String(120), primary_key=True),
    Column("user_id", String(120), primary_key=True),
    Column("text_unit_id", String(160), primary_key=True),
    Column("completed", Boolean(), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
bookmarks_table = Table(
    "bible_bookmarks", metadata,
    Column("tenant_id", String(120), primary_key=True),
    Column("user_id", String(120), primary_key=True),
    Column("text_unit_id", String(160), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
highlights_table = Table(
    "bible_highlights", metadata,
    Column("tenant_id", String(120), primary_key=True),
    Column("highlight_id", String(120), primary_key=True),
    Column("user_id", String(120), nullable=False),
    Column("text_unit_id", String(160), nullable=False),
    Column("note", Text(), nullable=False),
    Column("color", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
personal_table = Table(
    "bible_personal_entries", metadata,
    Column("tenant_id", String(120), primary_key=True),
    Column("entry_id", String(120), primary_key=True),
    Column("user_id", String(120), nullable=False),
    Column("kind", String(30), nullable=False),
    Column("ciphertext", Text(), nullable=False),
    Column("key_version", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

faith_preferences_table = Table(
    "bible_faith_preferences", metadata,
    Column("tenant_id", String(120), primary_key=True),
    Column("user_id", String(120), primary_key=True),
    Column("ciphertext", Text(), nullable=False),
    Column("key_version", String(20), nullable=False),
    Column("consent_version", String(60), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
