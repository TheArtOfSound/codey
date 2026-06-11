from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from starlette.requests import Request
from starlette.responses import Response

import codey.saas.auth.oauth as oauth
import codey.saas.auth.cookies as cookies
import codey.saas.auth.public_urls as public_urls
from codey.saas.auth.cookies import set_auth_cookie
from codey.saas.auth.oauth import decode_oauth_state, oauth_github_url, oauth_google_url


def test_github_connect_oauth_uses_requested_public_urls() -> None:
    url, state = oauth_github_url(
        intent="connect",
        frontend_origin="http://198.211.100.37",
        api_base_url="http://198.211.100.37/api/proxy",
    )
    query = parse_qs(urlparse(url).query)
    state_payload = decode_oauth_state(state, "github")

    assert query["redirect_uri"][0] == "http://198.211.100.37/api/proxy/auth/github/callback"
    assert query["scope"][0] == "read:user user:email repo read:org"
    assert state_payload["frontend_origin"] == "http://198.211.100.37"
    assert state_payload["api_base_url"] == "http://198.211.100.37/api/proxy"
    assert state_payload["intent"] == "connect"


def test_google_oauth_uses_requested_public_urls() -> None:
    url, state = oauth_google_url(
        frontend_origin="https://codey.imagineqira.com",
        api_base_url="https://codey.imagineqira.com/api/proxy",
    )
    query = parse_qs(urlparse(url).query)
    state_payload = decode_oauth_state(state, "google")

    assert query["redirect_uri"][0] == (
        "https://codey.imagineqira.com/api/proxy/auth/google/callback"
    )
    assert state_payload["frontend_origin"] == "https://codey.imagineqira.com"
    assert state_payload["api_base_url"] == "https://codey.imagineqira.com/api/proxy"


def test_github_oauth_url_trims_whitespace_padded_client_id(monkeypatch) -> None:
    monkeypatch.setattr(oauth.settings, "github_client_id", " github-client ")

    url, _ = oauth_github_url()

    assert parse_qs(urlparse(url).query)["client_id"][0] == "github-client"


def test_google_oauth_url_trims_whitespace_padded_client_id(monkeypatch) -> None:
    monkeypatch.setattr(oauth.settings, "google_client_id", " google-client ")

    url, _ = oauth_google_url()

    assert parse_qs(urlparse(url).query)["client_id"][0] == "google-client"


def test_oauth_state_rejects_malformed_public_urls() -> None:
    url, state = oauth_github_url(
        intent="connect",
        frontend_origin="app.example.com",
        api_base_url="api.example.com",
    )
    query = parse_qs(urlparse(url).query)
    state_payload = decode_oauth_state(state, "github")

    assert query["redirect_uri"][0] == "/auth/github/callback"
    assert "frontend_origin" not in state_payload
    assert "api_base_url" not in state_payload
    assert state_payload["intent"] == "connect"


def test_oauth_state_rejects_invalid_port_public_urls() -> None:
    url, state = oauth_github_url(
        intent="connect",
        frontend_origin="https://app.example.com:bad",
        api_base_url="https://api.example.com:bad",
    )
    query = parse_qs(urlparse(url).query)
    state_payload = decode_oauth_state(state, "github")

    assert query["redirect_uri"][0] == "/auth/github/callback"
    assert "frontend_origin" not in state_payload
    assert "api_base_url" not in state_payload
    assert state_payload["intent"] == "connect"


def test_oauth_state_rejects_zero_port_public_urls() -> None:
    url, state = oauth_github_url(
        intent="connect",
        frontend_origin="https://app.example.com:0",
        api_base_url="https://api.example.com:0/proxy",
    )
    query = parse_qs(urlparse(url).query)
    state_payload = decode_oauth_state(state, "github")

    assert query["redirect_uri"][0] == "/auth/github/callback"
    assert "frontend_origin" not in state_payload
    assert "api_base_url" not in state_payload
    assert state_payload["intent"] == "connect"


def test_auth_cookie_omits_domain_for_ip_origin() -> None:
    response = Response()

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="http://198.211.100.37",
        api_base_url="http://198.211.100.37/api/proxy",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Domain=" not in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" not in set_cookie


def test_auth_cookie_uses_shared_domain_for_custom_host() -> None:
    response = Response()

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="https://codey.imagineqira.com",
        api_base_url="https://codey.imagineqira.com/api/proxy",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Domain=.imagineqira.com" in set_cookie
    assert "Secure" in set_cookie


def test_auth_cookie_normalizes_dns_trailing_dot_hosts() -> None:
    response = Response()

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="https://app.example.com.",
        api_base_url="https://api.example.com./api",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Domain=.example.com;" in set_cookie


def test_auth_cookie_trims_whitespace_padded_explicit_urls() -> None:
    response = Response()

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin=" https://app.example.com ",
        api_base_url=" https://api.example.com/proxy ",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Domain=.example.com" in set_cookie
    assert "Secure" in set_cookie


def test_auth_cookie_trims_whitespace_padded_token() -> None:
    response = Response()

    set_auth_cookie(response, " test-token ")

    assert "codey_session=test-token" in response.headers["set-cookie"]


def test_auth_cookie_rejects_malformed_token_value() -> None:
    response = Response()

    for token in ("   ", "test-token bad", "test-token\nbad", "test-token\u00a0bad"):
        with pytest.raises(ValueError, match="Session cookie token"):
            set_auth_cookie(response, token)


def test_auth_cookie_secure_flag_follows_api_base_url() -> None:
    response = Response()

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="https://codey.imagineqira.com",
        api_base_url="http://codey.imagineqira.com/api/proxy",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Domain=.imagineqira.com" in set_cookie
    assert "Secure" not in set_cookie


def test_auth_cookie_rejects_traversal_api_base_url_for_secure_flag(monkeypatch) -> None:
    response = Response()
    monkeypatch.setattr(cookies, "get_public_api_base_url", lambda: "")
    monkeypatch.setattr(cookies, "get_public_frontend_origin", lambda: "")

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="http://app.example.com",
        api_base_url="https://api.example.com/proxy/../admin",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Secure" not in set_cookie


def test_auth_cookie_rejects_encoded_backslash_traversal_api_base_url(monkeypatch) -> None:
    response = Response()
    monkeypatch.setattr(cookies, "get_public_api_base_url", lambda: "")
    monkeypatch.setattr(cookies, "get_public_frontend_origin", lambda: "")

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="http://app.example.com",
        api_base_url="https://api.example.com/proxy/%5c..%5cadmin",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Secure" not in set_cookie


def test_auth_cookie_rejects_credentialed_api_base_url_for_secure_flag(
    monkeypatch,
) -> None:
    response = Response()
    monkeypatch.setattr(cookies, "get_public_api_base_url", lambda: "")
    monkeypatch.setattr(cookies, "get_public_frontend_origin", lambda: "")

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="http://app.example.com",
        api_base_url="https://user:pass@api.example.com/proxy",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Secure" not in set_cookie


def test_auth_cookie_rejects_invalid_port_api_base_url_for_secure_flag(
    monkeypatch,
) -> None:
    response = Response()
    monkeypatch.setattr(cookies, "get_public_api_base_url", lambda: "")
    monkeypatch.setattr(cookies, "get_public_frontend_origin", lambda: "")

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="http://app.example.com",
        api_base_url="https://api.example.com:bad/proxy",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Secure" not in set_cookie


def test_auth_cookie_rejects_zero_port_urls_for_secure_flag_and_domain(
    monkeypatch,
) -> None:
    response = Response()
    monkeypatch.setattr(cookies, "get_public_api_base_url", lambda: "")
    monkeypatch.setattr(cookies, "get_public_frontend_origin", lambda: "")

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="https://app.example.com:0",
        api_base_url="https://api.example.com:0/proxy",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Secure" not in set_cookie
    assert "Domain=" not in set_cookie


def test_auth_cookie_rejects_control_api_base_url_for_secure_flag(
    monkeypatch,
) -> None:
    response = Response()
    monkeypatch.setattr(cookies, "get_public_api_base_url", lambda: "")
    monkeypatch.setattr(cookies, "get_public_frontend_origin", lambda: "")

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="http://app.example.com",
        api_base_url="https://api.example.com/proxy\n?bad=1",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Secure" not in set_cookie


def test_auth_cookie_rejects_internal_whitespace_api_base_url_for_secure_flag(
    monkeypatch,
) -> None:
    response = Response()
    monkeypatch.setattr(cookies, "get_public_api_base_url", lambda: "")
    monkeypatch.setattr(cookies, "get_public_frontend_origin", lambda: "")

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="http://app.example.com",
        api_base_url="https://api.example.com/proxy bad",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Secure" not in set_cookie


def test_auth_cookie_omits_domain_for_mismatched_frontend_and_api_hosts() -> None:
    response = Response()

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="https://app.example.com",
        api_base_url="https://api.other-example.com/proxy",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Domain=" not in set_cookie
    assert "Secure" in set_cookie


def test_auth_cookie_uses_registrable_domain_for_multipart_public_suffixes() -> None:
    response = Response()

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="https://app.example.co.uk",
        api_base_url="https://api.example.co.uk/proxy",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Domain=.example.co.uk" in set_cookie


def test_auth_cookie_omits_domain_for_bare_multipart_public_suffixes() -> None:
    response = Response()

    set_auth_cookie(
        response,
        "test-token",
        frontend_origin="https://co.uk",
        api_base_url="https://co.uk/proxy",
    )

    set_cookie = response.headers["set-cookie"]
    assert "Domain=" not in set_cookie
    assert "Secure" in set_cookie


def test_auth_cookie_uses_normalized_settings_fallback(monkeypatch) -> None:
    response = Response()
    monkeypatch.setattr(public_urls.settings, "frontend_url", " https://app.example.com ")
    monkeypatch.setattr(public_urls.settings, "api_url", " https://api.example.com/proxy ")

    set_auth_cookie(response, "test-token")

    set_cookie = response.headers["set-cookie"]
    assert "Domain=.example.com" in set_cookie
    assert "Secure" in set_cookie


def test_public_urls_trim_whitespace_padded_request_headers() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [
                (
                    public_urls.FRONTEND_ORIGIN_HEADER.encode("latin-1"),
                    b" https://app.example.com ",
                ),
                (
                    public_urls.API_BASE_URL_HEADER.encode("latin-1"),
                    b" https://api.example.com/proxy/ ",
                ),
            ],
        }
    )

    assert public_urls.get_public_frontend_origin(request) == "https://app.example.com"
    assert public_urls.get_public_api_base_url(request) == "https://api.example.com/proxy"


def test_get_public_frontend_origin_rejects_whitespace_setting(monkeypatch) -> None:
    monkeypatch.setattr(public_urls.settings, "frontend_url", "   ")

    assert public_urls.get_public_frontend_origin() == ""


def test_get_public_frontend_origin_rejects_non_string_setting(monkeypatch) -> None:
    monkeypatch.setattr(
        public_urls.settings,
        "frontend_url",
        {"url": "https://app.example.com"},
    )

    assert public_urls.get_public_frontend_origin() == ""


def test_get_public_frontend_origin_rejects_malformed_setting(monkeypatch) -> None:
    monkeypatch.setattr(public_urls.settings, "frontend_url", "app.example.com")

    assert public_urls.get_public_frontend_origin() == ""


def test_get_public_frontend_origin_rejects_invalid_port_setting(monkeypatch) -> None:
    monkeypatch.setattr(public_urls.settings, "frontend_url", "https://app.example.com:bad")

    assert public_urls.get_public_frontend_origin() == ""

    monkeypatch.setattr(public_urls.settings, "frontend_url", "https://app.example.com:0")

    assert public_urls.get_public_frontend_origin() == ""


def test_get_public_frontend_origin_rejects_control_character_setting(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        public_urls.settings,
        "frontend_url",
        "https://app.example.com/\n.evil",
    )

    assert public_urls.get_public_frontend_origin() == ""


def test_get_public_frontend_origin_rejects_internal_whitespace_setting(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        public_urls.settings,
        "frontend_url",
        "https://app example.com",
    )

    assert public_urls.get_public_frontend_origin() == ""


def test_get_public_frontend_origin_rejects_credentialed_setting(monkeypatch) -> None:
    monkeypatch.setattr(
        public_urls.settings,
        "frontend_url",
        "https://user:pass@app.example.com",
    )

    assert public_urls.get_public_frontend_origin() == ""


def test_get_public_api_base_url_rejects_whitespace_setting(monkeypatch) -> None:
    monkeypatch.setattr(public_urls.settings, "api_url", "   ")

    assert public_urls.get_public_api_base_url() == ""


def test_get_public_api_base_url_rejects_non_string_setting(monkeypatch) -> None:
    monkeypatch.setattr(
        public_urls.settings,
        "api_url",
        {"url": "https://api.example.com/proxy"},
    )

    assert public_urls.get_public_api_base_url() == ""


def test_get_public_api_base_url_rejects_malformed_setting(monkeypatch) -> None:
    monkeypatch.setattr(public_urls.settings, "api_url", "api.example.com")

    assert public_urls.get_public_api_base_url() == ""


def test_get_public_api_base_url_rejects_invalid_port_setting(monkeypatch) -> None:
    monkeypatch.setattr(public_urls.settings, "api_url", "https://api.example.com:bad")

    assert public_urls.get_public_api_base_url() == ""

    monkeypatch.setattr(public_urls.settings, "api_url", "https://api.example.com:0/proxy")

    assert public_urls.get_public_api_base_url() == ""


def test_get_public_api_base_url_rejects_control_character_setting(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        public_urls.settings,
        "api_url",
        "https://api.example.com/proxy\r?bad=1",
    )

    assert public_urls.get_public_api_base_url() == ""


def test_get_public_api_base_url_rejects_internal_whitespace_setting(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        public_urls.settings,
        "api_url",
        "https://api.example.com/proxy bad",
    )

    assert public_urls.get_public_api_base_url() == ""


def test_get_public_api_base_url_rejects_traversal_paths(monkeypatch) -> None:
    monkeypatch.setattr(
        public_urls.settings,
        "api_url",
        "https://api.example.com/proxy/%2e%2e/admin",
    )

    assert public_urls.get_public_api_base_url() == ""


def test_get_public_api_base_url_rejects_encoded_backslash_traversal(monkeypatch) -> None:
    monkeypatch.setattr(
        public_urls.settings,
        "api_url",
        "https://api.example.com/proxy/%5c..%5cadmin",
    )

    assert public_urls.get_public_api_base_url() == ""


def test_build_callback_url_trims_api_base_url() -> None:
    callback_url = oauth._build_callback_url(
        "github",
        " https://api.example.com/proxy/ ",
    )

    assert callback_url == "https://api.example.com/proxy/auth/github/callback"


def test_build_callback_url_rejects_whitespace_settings_fallback(monkeypatch) -> None:
    monkeypatch.setattr(oauth.settings, "api_url", "   ")

    callback_url = oauth._build_callback_url("google")

    assert callback_url == "/auth/google/callback"


def test_build_callback_url_rejects_malformed_settings_fallback(monkeypatch) -> None:
    monkeypatch.setattr(oauth.settings, "api_url", "api.example.com")

    callback_url = oauth._build_callback_url("github")

    assert callback_url == "/auth/github/callback"


def test_build_callback_url_rejects_invalid_port_settings_fallback(monkeypatch) -> None:
    monkeypatch.setattr(oauth.settings, "api_url", "https://api.example.com:bad")

    callback_url = oauth._build_callback_url("github")

    assert callback_url == "/auth/github/callback"

    monkeypatch.setattr(oauth.settings, "api_url", "https://api.example.com:0/proxy")

    callback_url = oauth._build_callback_url("github")

    assert callback_url == "/auth/github/callback"


def test_build_callback_url_rejects_control_api_base_url(monkeypatch) -> None:
    monkeypatch.setattr(oauth.settings, "api_url", "   ")

    callback_url = oauth._build_callback_url(
        "github",
        "https://api.example.com/proxy\n?bad=1",
    )

    assert callback_url == "/auth/github/callback"


def test_oauth_public_url_normalizers_reject_control_characters() -> None:
    assert oauth._normalize_oauth_frontend_origin(
        "https://app.example.com/\r.evil"
    ) is None
    assert oauth._normalize_oauth_api_base_url(
        "https://api.example.com/proxy\n?bad=1"
    ) is None


def test_oauth_public_url_normalizers_reject_internal_whitespace() -> None:
    assert oauth._normalize_oauth_frontend_origin(
        "https://app example.com"
    ) is None
    assert oauth._normalize_oauth_api_base_url(
        "https://api.example.com/proxy bad"
    ) is None


def test_build_callback_url_rejects_traversal_api_base_url(monkeypatch) -> None:
    monkeypatch.setattr(oauth.settings, "api_url", "   ")

    callback_url = oauth._build_callback_url(
        "github",
        "https://api.example.com/proxy/../admin",
    )

    assert callback_url == "/auth/github/callback"


def test_build_callback_url_rejects_encoded_backslash_traversal_api_base_url(
    monkeypatch,
) -> None:
    monkeypatch.setattr(oauth.settings, "api_url", "   ")

    callback_url = oauth._build_callback_url(
        "github",
        "https://api.example.com/proxy/%5c..%5cadmin",
    )

    assert callback_url == "/auth/github/callback"


def test_build_callback_url_rejects_credentialed_api_base_url(monkeypatch) -> None:
    monkeypatch.setattr(oauth.settings, "api_url", "   ")

    callback_url = oauth._build_callback_url(
        "github",
        "https://user:pass@api.example.com/proxy",
    )

    assert callback_url == "/auth/github/callback"
