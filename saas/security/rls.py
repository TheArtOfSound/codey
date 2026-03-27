from __future__ import annotations

"""Row Level Security (RLS) setup for PostgreSQL.

These SQL statements are intended to be executed inside an Alembic migration or
a bootstrap script.  They ensure that every query scoped to a database
connection can only see rows belonging to the authenticated user, providing
defence-in-depth on top of application-level ownership checks.

Usage in a migration::

    from codey.saas.security.rls import (
        SQL_SET_USER_FUNCTION,
        SQL_ENABLE_RLS,
        SQL_CREATE_POLICIES,
    )

    def upgrade():
        op.execute(SQL_SET_USER_FUNCTION)
        for stmt in SQL_ENABLE_RLS:
            op.execute(stmt)
        for stmt in SQL_CREATE_POLICIES:
            op.execute(stmt)

At connection time (e.g. in a FastAPI dependency or event hook) call::

    await session.execute(text("SELECT set_current_user_id(:uid)"), {"uid": str(user.id)})

before running any tenant-scoped queries.
"""

# ---------------------------------------------------------------------------
# Tables that require RLS
# ---------------------------------------------------------------------------

_RLS_TABLES: list[str] = [
    "coding_sessions",
    "repositories",
    "credit_transactions",
    "user_memory",
    "projects",
    "project_versions",
    "exports",
]

# ---------------------------------------------------------------------------
# Function to set the current user at connection time
# ---------------------------------------------------------------------------

SQL_SET_USER_FUNCTION: str = """
CREATE OR REPLACE FUNCTION set_current_user_id(uid TEXT)
RETURNS VOID AS $$
BEGIN
    PERFORM set_config('app.current_user_id', uid, true);
END;
$$ LANGUAGE plpgsql;
""".strip()

# ---------------------------------------------------------------------------
# Enable RLS on each table
# ---------------------------------------------------------------------------

SQL_ENABLE_RLS: list[str] = [
    f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" for table in _RLS_TABLES
]

# Force RLS even for table owners (prevents bypassing in superuser sessions
# used by the application — remove if the app role is not a table owner).
SQL_FORCE_RLS: list[str] = [
    f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" for table in _RLS_TABLES
]

# ---------------------------------------------------------------------------
# Row-level policies — one per table.
#
# Each policy restricts SELECT, INSERT, UPDATE, and DELETE to rows where
# ``user_id`` matches the session-level GUC ``app.current_user_id``.
# ---------------------------------------------------------------------------

SQL_CREATE_POLICIES: list[str] = [
    f"""
CREATE POLICY user_isolation_{table} ON {table}
    FOR ALL
    USING (user_id = current_setting('app.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('app.current_user_id')::UUID);
""".strip()
    for table in _RLS_TABLES
]

# ---------------------------------------------------------------------------
# Convenience: single string to run everything in one shot
# ---------------------------------------------------------------------------

SQL_FULL_SETUP: str = "\n\n".join(
    [SQL_SET_USER_FUNCTION]
    + SQL_ENABLE_RLS
    + SQL_FORCE_RLS
    + SQL_CREATE_POLICIES
)
