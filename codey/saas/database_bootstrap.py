from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
except ModuleNotFoundError as exc:
    if exc.name != "sqlalchemy":
        raise
    _SQLALCHEMY_IMPORT_ERROR = exc
    text = None  # type: ignore[assignment]
    AsyncConnection = Any  # type: ignore[misc, assignment]
    AsyncEngine = Any  # type: ignore[misc, assignment]
else:
    _SQLALCHEMY_IMPORT_ERROR = None

CURRENT_APP_USER_FUNCTION_SQL = """
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
$$ LANGUAGE plpgsql STABLE;
""".strip()

DATABASE_BOOTSTRAP_LOCK_SQL = "SELECT pg_advisory_xact_lock(924601, 4102026)"

PUBLIC_RLS_DISABLED_TABLES: dict[str, tuple[str, ...]] = {
    "users": ("users_self",),
    "security_audit_log": ("security_audit_log_owner",),
}


@dataclass(frozen=True)
class PolicySpec:
    table: str
    name: str
    using: str
    with_check: str | None = None

    def create_sql(self) -> str:
        check = self.with_check or self.using
        return (
            f"CREATE POLICY {self.name} ON {self.table} "
            f"FOR ALL USING ({self.using}) WITH CHECK ({check})"
        )


APP_RLS_POLICIES: tuple[PolicySpec, ...] = (
    PolicySpec(
        "credit_transactions",
        "credit_transactions_owner",
        "user_id = codey_current_user_id()",
    ),
    PolicySpec("coding_sessions", "coding_sessions_owner", "user_id = codey_current_user_id()"),
    PolicySpec("repositories", "repositories_owner", "user_id = codey_current_user_id()"),
    PolicySpec("user_memory", "user_memory_owner", "user_id = codey_current_user_id()"),
    PolicySpec(
        "memory_update_logs",
        "memory_update_logs_owner",
        "user_id = codey_current_user_id()",
    ),
    PolicySpec("projects", "projects_owner", "user_id = codey_current_user_id()"),
    PolicySpec(
        "project_versions",
        "project_versions_owner",
        "project_id IN (SELECT id FROM projects WHERE user_id = codey_current_user_id())",
    ),
    PolicySpec("exports", "exports_owner", "user_id = codey_current_user_id()"),
    PolicySpec(
        "referrals",
        "referrals_owner",
        "referrer_id = codey_current_user_id() OR referred_id = codey_current_user_id()",
    ),
    PolicySpec("session_costs", "session_costs_owner", "user_id = codey_current_user_id()"),
    PolicySpec("api_keys", "api_keys_owner", "user_id = codey_current_user_id()"),
    PolicySpec("build_projects", "build_projects_owner", "user_id = codey_current_user_id()"),
    PolicySpec(
        "build_files",
        "build_files_owner",
        "project_id IN (SELECT id FROM build_projects WHERE user_id = codey_current_user_id())",
    ),
    PolicySpec(
        "build_checkpoints",
        "build_checkpoints_owner",
        "project_id IN (SELECT id FROM build_projects WHERE user_id = codey_current_user_id())",
    ),
    PolicySpec("project_memories", "project_memories_owner", "user_id = codey_current_user_id()"),
    PolicySpec(
        "cost_overflow_events",
        "cost_overflow_events_owner",
        "user_id = codey_current_user_id()",
    ),
)


def _coerce_table_exists(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return False


def _require_sqlalchemy() -> None:
    if _SQLALCHEMY_IMPORT_ERROR is not None:
        raise RuntimeError(
            "SQLAlchemy is required for database bootstrap"
        ) from _SQLALCHEMY_IMPORT_ERROR


def _sql_text(statement: str) -> Any:
    _require_sqlalchemy()
    assert text is not None
    return text(statement)


async def _table_exists(conn: AsyncConnection, table_name: str) -> bool:
    result = await conn.execute(
        _sql_text("SELECT to_regclass(:table_name) IS NOT NULL"),
        {"table_name": f"public.{table_name}"},
    )
    return _coerce_table_exists(result.scalar())


async def ensure_database_compatibility(engine: AsyncEngine) -> None:
    """Repair legacy Supabase-style RLS so SQLAlchemy app sessions can work.

    Existing deployments were bootstrapped with policies that depend on
    ``auth.uid()``, which does not exist for normal app-managed database
    sessions. This startup repair is idempotent and safely aligns the active
    database with the app-managed ``app.current_user_id`` convention.
    """

    _require_sqlalchemy()

    async with engine.begin() as conn:
        # Multiple Uvicorn workers can enter startup together during deploys.
        # Serialize the compatibility DDL to avoid PostgreSQL catalog races.
        await conn.execute(_sql_text(DATABASE_BOOTSTRAP_LOCK_SQL))
        await conn.execute(_sql_text(CURRENT_APP_USER_FUNCTION_SQL))

        for table_name, policies in PUBLIC_RLS_DISABLED_TABLES.items():
            if not await _table_exists(conn, table_name):
                continue
            await conn.execute(
                _sql_text(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
            )
            for policy_name in policies:
                await conn.execute(
                    _sql_text(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}")
                )

        for policy in APP_RLS_POLICIES:
            if not await _table_exists(conn, policy.table):
                continue
            await conn.execute(
                _sql_text(f"ALTER TABLE {policy.table} ENABLE ROW LEVEL SECURITY")
            )
            await conn.execute(
                _sql_text(f"DROP POLICY IF EXISTS {policy.name} ON {policy.table}")
            )
            await conn.execute(_sql_text(policy.create_sql()))
