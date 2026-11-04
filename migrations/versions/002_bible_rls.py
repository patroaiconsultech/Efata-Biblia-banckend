"""enable tenant and user row-level security

Revision ID: 002_bible_rls
Revises: 001_bible_foundation
"""
from alembic import op

revision = "002_bible_rls"
down_revision = "001_bible_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("bible_threads", "bible_messages", "bible_progress", "bible_personal_entries"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY bible_threads_isolation ON bible_threads
        USING (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
    """)
    op.execute("""
        CREATE POLICY bible_messages_isolation ON bible_messages
        USING (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
    """)
    op.execute("""
        CREATE POLICY bible_progress_isolation ON bible_progress
        USING (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
    """)
    op.execute("""
        CREATE POLICY bible_personal_isolation ON bible_personal_entries
        USING (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true) AND user_id = current_setting('app.user_id', true))
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, policy in (
        ("bible_personal_entries", "bible_personal_isolation"),
        ("bible_progress", "bible_progress_isolation"),
        ("bible_messages", "bible_messages_isolation"),
        ("bible_threads", "bible_threads_isolation"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
