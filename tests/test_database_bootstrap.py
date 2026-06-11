from codey.saas.database_bootstrap import (
    APP_RLS_POLICIES,
    CURRENT_APP_USER_FUNCTION_SQL,
    DATABASE_BOOTSTRAP_LOCK_SQL,
    PUBLIC_RLS_DISABLED_TABLES,
    _SQLALCHEMY_IMPORT_ERROR,
    _coerce_table_exists,
    _require_sqlalchemy,
)


def test_database_bootstrap_avoids_supabase_auth_uid() -> None:
    assert "auth.uid()" not in CURRENT_APP_USER_FUNCTION_SQL
    assert "app.current_user_id" in CURRENT_APP_USER_FUNCTION_SQL
    assert "pg_advisory_xact_lock" in DATABASE_BOOTSTRAP_LOCK_SQL

    for policy in APP_RLS_POLICIES:
        assert "auth.uid()" not in policy.using
        create_sql = policy.create_sql()
        assert "codey_current_user_id()" in create_sql


def test_public_tables_are_explicitly_left_app_managed() -> None:
    assert PUBLIC_RLS_DISABLED_TABLES["users"] == ("users_self",)
    assert PUBLIC_RLS_DISABLED_TABLES["security_audit_log"] == (
        "security_audit_log_owner",
    )


def test_table_exists_probe_only_trusts_boolean_scalars() -> None:
    assert _coerce_table_exists(True) is True
    assert _coerce_table_exists(False) is False
    assert _coerce_table_exists(None) is False
    assert _coerce_table_exists("true") is False
    assert _coerce_table_exists(1) is False


def test_database_bootstrap_requires_sqlalchemy_for_database_calls() -> None:
    if _SQLALCHEMY_IMPORT_ERROR is None:
        return

    try:
        _require_sqlalchemy()
    except RuntimeError as exc:
        assert str(exc) == "SQLAlchemy is required for database bootstrap"
    else:
        raise AssertionError("expected missing SQLAlchemy to fail bootstrap access")
