"""bookmarks and highlights

Revision ID: 004_bible_bookmarks_highlights
Revises: 003_bible_content
"""
from alembic import op
import sqlalchemy as sa

revision = "004_bible_bookmarks_highlights"
down_revision = "003_bible_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bible_bookmarks",
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("text_unit_id", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", "text_unit_id"),
    )
    op.create_table(
        "bible_highlights",
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("highlight_id", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("text_unit_id", sa.String(length=160), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("color", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "highlight_id"),
    )
    op.create_index("ix_bible_highlights_owner", "bible_highlights", ["tenant_id", "user_id"])
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("bible_bookmarks", "bible_highlights"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY bible_bookmarks_isolation ON bible_bookmarks
        USING (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
    """)
    op.execute("""
        CREATE POLICY bible_highlights_isolation ON bible_highlights
        USING (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS bible_highlights_isolation ON bible_highlights")
        op.execute("DROP POLICY IF EXISTS bible_bookmarks_isolation ON bible_bookmarks")
    op.drop_index("ix_bible_highlights_owner", table_name="bible_highlights")
    op.drop_table("bible_highlights")
    op.drop_table("bible_bookmarks")
