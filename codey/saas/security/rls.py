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
# Tables that require RLS.
#
# The ``users`` and ``security_audit_log`` tables are intentionally excluded
# because unauthenticated signup/login and internal audit writes must work
# before an authenticated user context exists.
# ---------------------------------------------------------------------------

_RLS_POLICY_EXPRESSIONS: dict[str, str] = {
    "credit_transactions": "user_id = codey_current_user_id()",
    "coding_sessions": "user_id = codey_current_user_id()",
    "repositories": "user_id = codey_current_user_id()",
    "user_memory": "user_id = codey_current_user_id()",
    "memory_update_logs": "user_id = codey_current_user_id()",
    "projects": "user_id = codey_current_user_id()",
    "project_versions": (
        "project_id IN (SELECT id FROM projects WHERE user_id = codey_current_user_id())"
    ),
    "exports": "user_id = codey_current_user_id()",
    "referrals": (
        "referrer_id = codey_current_user_id() OR referred_id = codey_current_user_id()"
    ),
    "session_costs": "user_id = codey_current_user_id()",
    "api_keys": "user_id = codey_current_user_id()",
    "build_projects": "user_id = codey_current_user_id()",
    "build_files": (
        "project_id IN (SELECT id FROM build_projects WHERE user_id = codey_current_user_id())"
    ),
    "build_checkpoints": (
        "project_id IN (SELECT id FROM build_projects WHERE user_id = codey_current_user_id())"
    ),
    "project_memories": "user_id = codey_current_user_id()",
    "cost_overflow_events": "user_id = codey_current_user_id()",
}
_RLS_TABLES: list[str] = list(_RLS_POLICY_EXPRESSIONS)

# ---------------------------------------------------------------------------
# Function to set the current user at connection time
# ---------------------------------------------------------------------------

SQL_SET_USER_FUNCTION: str = """
CREATE OR REPLACE FUNCTION codey_current_user_id()
RETURNS UUID AS $$
DECLARE
    raw_uid TEXT;
BEGIN
    raw_uid := nullif(current_setting('app.current_user_id', true), '');
    IF raw_uid IS NULL THEN
        RETURN NULL;
    END IF;

    RETURN raw_uid::UUID;
EXCEPTION
    WHEN invalid_text_representation THEN
        RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION set_current_user_id(uid TEXT)
RETURNS VOID AS $$
BEGIN
    PERFORM set_config('app.current_user_id', COALESCE(uid, ''), true);
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
# Each policy restricts SELECT, INSERT, UPDATE, and DELETE to rows owned by
# the session-level GUC ``app.current_user_id``. Child tables use parent-row
# ownership subqueries when they do not carry their own ``user_id`` column.
# ---------------------------------------------------------------------------

SQL_CREATE_POLICIES: list[str] = [
    f"""
DROP POLICY IF EXISTS user_isolation_{table} ON {table};
CREATE POLICY user_isolation_{table} ON {table}
    FOR ALL
    USING ({expression})
    WITH CHECK ({expression});
""".strip()
    for table, expression in _RLS_POLICY_EXPRESSIONS.items()
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
