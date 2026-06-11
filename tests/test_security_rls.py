from __future__ import annotations

from codey.saas.database_bootstrap import APP_RLS_POLICIES
from codey.saas.security import rls


def test_rls_helper_covers_bootstrap_policy_tables() -> None:
    bootstrap_tables = {policy.table for policy in APP_RLS_POLICIES}

    assert set(rls._RLS_POLICY_EXPRESSIONS) == bootstrap_tables
    assert "users" not in rls._RLS_POLICY_EXPRESSIONS
    assert "security_audit_log" not in rls._RLS_POLICY_EXPRESSIONS


def test_rls_helper_uses_parent_ownership_for_child_tables() -> None:
    project_version_policy = "\n".join(
        sql for sql in rls.SQL_CREATE_POLICIES if " ON project_versions" in sql
    )
    build_file_policy = "\n".join(
        sql for sql in rls.SQL_CREATE_POLICIES if " ON build_files" in sql
    )
    build_checkpoint_policy = "\n".join(
        sql for sql in rls.SQL_CREATE_POLICIES if " ON build_checkpoints" in sql
    )

    assert "project_id IN (SELECT id FROM projects" in project_version_policy
    assert "project_id IN (SELECT id FROM build_projects" in build_file_policy
    assert "project_id IN (SELECT id FROM build_projects" in build_checkpoint_policy
    assert "USING (user_id = codey_current_user_id())" not in project_version_policy
    assert "USING (user_id = codey_current_user_id())" not in build_file_policy
    assert "USING (user_id = codey_current_user_id())" not in build_checkpoint_policy


def test_rls_helper_policy_sql_is_idempotent() -> None:
    for table in rls._RLS_POLICY_EXPRESSIONS:
        policy_sql = "\n".join(
            sql for sql in rls.SQL_CREATE_POLICIES if f" ON {table}" in sql
        )
        drop_stmt = f"DROP POLICY IF EXISTS user_isolation_{table} ON {table};"
        create_stmt = f"CREATE POLICY user_isolation_{table} ON {table}"

        assert drop_stmt in policy_sql
        assert create_stmt in policy_sql
        assert policy_sql.index(drop_stmt) < policy_sql.index(create_stmt)
