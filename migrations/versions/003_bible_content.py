"""content and rights registry

Revision ID: 003_bible_content
Revises: 002_bible_rls
"""
from alembic import op
import sqlalchemy as sa

revision = "003_bible_content"
down_revision = "002_bible_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bible_corpora",
        sa.Column("corpus_id", sa.String(length=120), primary_key=True),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
    )
    op.create_table(
        "bible_sources",
        sa.Column("source_id", sa.String(length=120), primary_key=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
    )
    op.create_table(
        "bible_rights_records",
        sa.Column("rights_record_id", sa.String(length=120), primary_key=True),
        sa.Column("source_id", sa.String(length=120), nullable=False),
        sa.Column("commercial_use", sa.Boolean(), nullable=False),
        sa.Column("redistribution", sa.Boolean(), nullable=False),
        sa.Column("modification", sa.Boolean(), nullable=False),
        sa.Column("ai_processing", sa.Boolean(), nullable=False),
        sa.Column("rag_ingestion", sa.Boolean(), nullable=False),
        sa.Column("attribution_required", sa.Boolean(), nullable=False),
        sa.Column("legal_review", sa.String(length=30), nullable=False),
    )
    op.create_table(
        "bible_works",
        sa.Column("work_id", sa.String(length=120), primary_key=True),
        sa.Column("corpus_id", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False, unique=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("testament", sa.String(length=30), nullable=False),
        sa.Column("book_order", sa.Integer(), nullable=False),
    )
    op.create_table(
        "bible_translations",
        sa.Column("translation_id", sa.String(length=120), primary_key=True),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.String(length=120), nullable=False),
        sa.Column("rights_record_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
    )
    op.execute("""
        INSERT INTO bible_corpora(corpus_id, name, description, status)
        VALUES ('canonical_scripture', 'Escritura — Bíblia Livre', 'Corpus bíblico em português com proveniência registrada.', 'active')
    """)
    op.execute("""
        INSERT INTO bible_sources(source_id, title, url, version, attribution, status)
        VALUES ('bible-livre', 'Bíblia Livre (BLIVRE)', 'https://github.com/blivre/BibliaLivre', '2025.1.0', 'Diego Santos, Mario Sérgio e Marco Teles; CC BY 3.0 BR.', 'verified')
    """)
    op.execute("""
        INSERT INTO bible_rights_records(rights_record_id, source_id, commercial_use, redistribution, modification, ai_processing, rag_ingestion, attribution_required, legal_review)
        VALUES ('rights_bible_livre', 'bible-livre', true, true, true, true, true, true, 'pending')
    """)


def downgrade() -> None:
    op.drop_table("bible_translations")
    op.drop_table("bible_works")
    op.drop_table("bible_rights_records")
    op.drop_table("bible_sources")
    op.drop_table("bible_corpora")
