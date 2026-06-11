from __future__ import annotations

from codey.saas.redis_url import normalize_redis_url


def test_normalize_redis_url_strips_padding_and_preserves_query() -> None:
    normalized = normalize_redis_url(" rediss://:pass@example.com:6379/0?foo=1 ")

    assert normalized == "rediss://:pass@example.com:6379/0?foo=1&ssl_cert_reqs=CERT_NONE"


def test_normalize_redis_url_replaces_blank_ssl_cert_reqs() -> None:
    normalized = normalize_redis_url("rediss://:pass@example.com:6379/0?ssl_cert_reqs=")

    assert normalized == "rediss://:pass@example.com:6379/0?ssl_cert_reqs=CERT_NONE"


def test_normalize_redis_url_preserves_explicit_ssl_cert_reqs() -> None:
    normalized = normalize_redis_url(
        "rediss://:pass@example.com:6379/0?foo=1&ssl_cert_reqs=required"
    )

    assert normalized == "rediss://:pass@example.com:6379/0?foo=1&ssl_cert_reqs=required"


def test_normalize_redis_url_prefers_explicit_duplicate_ssl_cert_reqs() -> None:
    normalized = normalize_redis_url(
        "rediss://:pass@example.com:6379/0?ssl_cert_reqs=required&ssl_cert_reqs="
    )

    assert normalized == "rediss://:pass@example.com:6379/0?ssl_cert_reqs=required"


def test_normalize_redis_url_rejects_invalid_ports() -> None:
    assert normalize_redis_url("redis://localhost:not-a-port/0") == ""
    assert normalize_redis_url("rediss://localhost:not-a-port/0") == ""
    assert normalize_redis_url("redis://localhost:0/0") == ""
    assert normalize_redis_url("rediss://localhost:0/0") == ""


def test_normalize_redis_url_rejects_missing_hosts() -> None:
    assert normalize_redis_url("redis:///0") == ""
    assert normalize_redis_url("rediss:///0") == ""
    assert normalize_redis_url("redis://:pass@/0") == ""
    assert normalize_redis_url("rediss://:pass@/0") == ""


def test_normalize_redis_url_fails_closed_for_blank_values() -> None:
    assert normalize_redis_url("   ") == ""


def test_normalize_redis_url_fails_closed_for_control_characters() -> None:
    assert normalize_redis_url("redis://localhost:6379/0\n?bad=1") == ""
    assert normalize_redis_url("rediss://localhost:6379/0\r?bad=1") == ""
    assert normalize_redis_url("redis://localhost:6379/\x7f0") == ""


def test_normalize_redis_url_fails_closed_for_internal_whitespace() -> None:
    assert normalize_redis_url("redis://local host:6379/0") == ""
    assert normalize_redis_url("rediss://localhost:6379/0 ?foo=1") == ""
    assert normalize_redis_url("redis://localhost:6379/0\u00a0bad") == ""


def test_normalize_redis_url_fails_closed_for_fragments() -> None:
    assert normalize_redis_url("redis://localhost:6379/0#debug") == ""
    assert normalize_redis_url("rediss://localhost:6379/0#debug") == ""


def test_normalize_redis_url_fails_closed_for_non_string_values() -> None:
    assert normalize_redis_url(None) == ""


def test_normalize_redis_url_fails_closed_for_unsupported_schemes() -> None:
    assert normalize_redis_url("http://localhost:6379/0") == ""
