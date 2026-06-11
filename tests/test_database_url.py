from __future__ import annotations

import uuid

import codey.saas.database as database


def test_normalized_database_url_strips_padding_and_removes_sslmode(monkeypatch) -> None:
    monkeypatch.setattr(
        database.settings,
        "database_url",
        " postgresql+asyncpg://user:pass@db.example.com/codey?sslmode=REQUIRE&pooler=1 ",
    )

    normalized_url, sslmode = database._normalized_database_url()

    assert normalized_url == "postgresql+asyncpg://user:pass@db.example.com/codey?pooler=1"
    assert sslmode == "require"


def test_normalized_database_url_upgrades_platform_postgres_scheme(monkeypatch) -> None:
    monkeypatch.setattr(
        database.settings,
        "database_url",
        "postgres://user:pass@db.example.com/codey?sslmode=require",
    )

    normalized_url, sslmode = database._normalized_database_url()

    assert normalized_url == "postgresql+asyncpg://user:pass@db.example.com/codey"
    assert sslmode == "require"


def test_normalized_database_url_prefers_explicit_duplicate_sslmode(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        database.settings,
        "database_url",
        (
            "postgresql://user:pass@db.example.com/codey"
            "?sslmode=&pooler=1&sslmode=REQUIRE"
        ),
    )

    normalized_url, sslmode = database._normalized_database_url()

    assert normalized_url == "postgresql+asyncpg://user:pass@db.example.com/codey?pooler=1"
    assert sslmode == "require"


def test_normalized_database_url_fails_closed_for_blank_values(monkeypatch) -> None:
    monkeypatch.setattr(database.settings, "database_url", "   ")

    assert database._normalized_database_url() == ("", "")


def test_normalized_database_url_fails_closed_for_non_string_values(monkeypatch) -> None:
    monkeypatch.setattr(database.settings, "database_url", None)

    assert database._normalized_database_url() == ("", "")


def test_normalized_database_url_fails_closed_for_unsupported_schemes(monkeypatch) -> None:
    monkeypatch.setattr(database.settings, "database_url", "sqlite+aiosqlite:///tmp/codey.db")

    assert database._normalized_database_url() == ("", "")


def test_normalized_database_url_fails_closed_for_invalid_ports(monkeypatch) -> None:
    monkeypatch.setattr(
        database.settings,
        "database_url",
        "postgresql://user:pass@db.example.com:not-a-port/codey",
    )

    assert database._normalized_database_url() == ("", "")

    monkeypatch.setattr(
        database.settings,
        "database_url",
        "postgresql://user:pass@db.example.com:0/codey",
    )

    assert database._normalized_database_url() == ("", "")


def test_normalized_database_url_fails_closed_for_control_characters(monkeypatch) -> None:
    monkeypatch.setattr(
        database.settings,
        "database_url",
        "postgresql://user:pass@db.example.com/codey\n?sslmode=require",
    )

    assert database._normalized_database_url() == ("", "")


def test_normalized_database_url_fails_closed_for_internal_whitespace(
    monkeypatch,
) -> None:
    for database_url in (
        "postgresql://user:pass@db example.com/codey",
        "postgresql://user:pass@db.example.com/codey ?sslmode=require",
        "postgresql://user:pass@db.example.com/codey\u00a0bad",
    ):
        monkeypatch.setattr(database.settings, "database_url", database_url)

        assert database._normalized_database_url() == ("", "")


def test_normalized_database_url_fails_closed_for_fragments(monkeypatch) -> None:
    monkeypatch.setattr(
        database.settings,
        "database_url",
        "postgresql://user:pass@db.example.com/codey#debug",
    )

    assert database._normalized_database_url() == ("", "")


def test_normalized_database_url_fails_closed_for_missing_hosts(monkeypatch) -> None:
    monkeypatch.setattr(database.settings, "database_url", "postgresql+asyncpg:///codey")

    assert database._normalized_database_url() == ("", "")

    monkeypatch.setattr(
        database.settings,
        "database_url",
        "postgresql+asyncpg://user:pass@/codey",
    )

    assert database._normalized_database_url() == ("", "")


def test_require_database_url_rejects_blank_values() -> None:
    try:
        database._require_database_url("")
    except RuntimeError as exc:
        assert str(exc) == "DATABASE_URL must not be blank"
    else:
        raise AssertionError("expected blank database URL to fail")


def test_database_access_requires_sqlalchemy_when_dependency_is_missing() -> None:
    if database._SQLALCHEMY_IMPORT_ERROR is None:
        return

    assert database.engine is None
    assert database.async_session_factory is None

    try:
        database._require_sqlalchemy()
    except RuntimeError as exc:
        assert str(exc) == "SQLAlchemy is required for database access"
    else:
        raise AssertionError("expected missing SQLAlchemy to fail database access")


def test_normalize_db_user_context_id_accepts_uuid_values() -> None:
    user_id = uuid.uuid4()

    assert database._normalize_db_user_context_id(user_id) == str(user_id)
    assert database._normalize_db_user_context_id(f" {user_id} ") == str(user_id)


def test_normalize_db_user_context_id_rejects_invalid_values() -> None:
    for value in (None, "", "not-a-uuid", ["user-1"]):
        try:
            database._normalize_db_user_context_id(value)
        except ValueError as exc:
            assert str(exc) == "Database user context requires a valid UUID"
        else:
            raise AssertionError(f"expected invalid context id to fail: {value!r}")
