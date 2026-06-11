from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from codey.saas.intelligence import research


def test_research_engine_normalizes_whitespace_only_secrets(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "   ")
    monkeypatch.setenv("BRAVE_API_KEY", "   ")
    monkeypatch.setenv("EXA_API_KEY", "   ")
    monkeypatch.setenv("GITHUB_TOKEN", "   ")

    engine = research.ResearchEngine()

    assert engine._tavily_key is None
    assert engine._brave_key is None
    assert engine._exa_key is None
    assert engine._github_token is None


def test_research_engine_rejects_malformed_secrets(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly bad")
    monkeypatch.setenv("BRAVE_API_KEY", "brave\nbad")
    monkeypatch.setenv("EXA_API_KEY", "exa\x7fbad")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token bad")

    engine = research.ResearchEngine()

    assert engine._tavily_key is None
    assert engine._brave_key is None
    assert engine._exa_key is None
    assert engine._github_token is None


def test_redact_research_error_removes_url_and_named_secrets() -> None:
    message = research._redact_research_error(
        "failed https://user:secret@example.test/search?api_key=tvly-secret&client_secret=client-secret"
        " token=gh-token auth_token=auth-secret refresh_token=refresh-secret"
        " password=pw-secret authorization: Bearer gh-auth for user@example.com"
    )

    assert "user@example.com" not in message
    assert "user:secret" not in message
    assert "secret@example.test" not in message
    assert "tvly-secret" not in message
    assert "client-secret" not in message
    assert "gh-token" not in message
    assert "auth-secret" not in message
    assert "refresh-secret" not in message
    assert "pw-secret" not in message
    assert "gh-auth" not in message
    assert "***@example.com" in message
    assert "https://***@example.test/search?api_key=***&client_secret=***" in message
    assert "token=***" in message
    assert "auth_token=***" in message
    assert "refresh_token=***" in message
    assert "password=***" in message
    assert "authorization: Bearer ***" in message


@pytest.mark.asyncio
async def test_search_web_redacts_provider_failure_logs(monkeypatch, caplog) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    engine = research.ResearchEngine()

    async def fail_tavily(_query, _max_results):
        raise RuntimeError(
            "request failed https://user:secret@example.test/search?api_key=tvly-secret"
        )

    monkeypatch.setattr(engine, "_search_tavily", fail_tavily)
    caplog.set_level(logging.WARNING, logger="codey.saas.intelligence.research")

    assert await engine.search_web("cache invalidation") == []
    assert "secret" not in caplog.text.lower()
    assert "tvly-secret" not in caplog.text
    assert "https://***@example.test/search?api_key=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_library_lookup_status_warnings_redact_library_names(
    monkeypatch,
    caplog,
) -> None:
    class _StatusClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, *args, **kwargs):
            return type("Response", (), {"status_code": 503})()

        async def post(self, *args, **kwargs):
            return type("Response", (), {"status_code": 503})()

    monkeypatch.setattr(research, "_HTTPX_IMPORT_ERROR", None)
    monkeypatch.setattr(
        research.httpx,
        "AsyncClient",
        lambda timeout: _StatusClient(),
    )
    caplog.set_level(logging.WARNING, logger="codey.saas.intelligence.research")

    engine = research.ResearchEngine()
    library = "pkg-user@example.com/https://user:secret@example.test/pkg?token=library-token"
    info = research.LibraryInfo(name=library)

    await engine._fetch_pypi(library, info)
    await engine._fetch_npm(library, info)
    await engine._fetch_crates(library, info)
    await engine._check_osv_vulns(library, "PyPI", info)

    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text.lower()
    assert "library-token" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/pkg?token=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_search_code_skips_http_when_github_token_is_whitespace(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "   ")

    class _UnexpectedClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("http client should not be created")

    monkeypatch.setattr(research.httpx, "AsyncClient", _UnexpectedClient)

    engine = research.ResearchEngine()

    assert await engine.search_code("cache invalidation") == []


@pytest.mark.asyncio
async def test_search_code_skips_http_when_github_token_has_control_character(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token\tbad")

    class _UnexpectedClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("http client should not be created")

    monkeypatch.setattr(research.httpx, "AsyncClient", _UnexpectedClient)

    engine = research.ResearchEngine()

    assert await engine.search_code("cache invalidation") == []


class _ResearchResponse:
    status_code = 200

    def __init__(self, payload) -> None:
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _ResearchClient:
    def __init__(self, *, payload) -> None:
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def get(self, *args, **kwargs):
        return _ResearchResponse(self._payload)

    async def post(self, *args, **kwargs):
        return _ResearchResponse(self._payload)


@pytest.mark.asyncio
async def test_search_providers_skip_malformed_result_entries(monkeypatch) -> None:
    engine = research.ResearchEngine()

    monkeypatch.setattr(
        research,
        "_http_client",
        lambda: _ResearchClient(
            payload={"results": ["bad", {"title": "ok", "content": 42}]}
        ),
    )
    tavily = await engine._search_tavily("cache invalidation", 5)

    monkeypatch.setattr(
        research,
        "_http_client",
        lambda: _ResearchClient(
            payload={"web": {"results": [{"title": 99, "description": "brave"}]}}
        ),
    )
    brave = await engine._search_brave("cache invalidation", 5)

    monkeypatch.setattr(
        research,
        "_http_client",
        lambda: _ResearchClient(
            payload={"results": [None, {"title": "exa", "highlight": "highlight"}]}
        ),
    )
    exa = await engine._search_exa("cache invalidation", 5)

    assert tavily == [
        research.SearchResult(title="ok", url="", snippet="", source="tavily")
    ]
    assert brave == [
        research.SearchResult(title="", url="", snippet="brave", source="brave")
    ]
    assert exa == [
        research.SearchResult(title="exa", url="", snippet="highlight", source="exa")
    ]


@pytest.mark.asyncio
async def test_search_code_skips_malformed_github_entries(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token")
    monkeypatch.setattr(
        research,
        "_http_client",
        lambda: _ResearchClient(
            payload={
                "items": [
                    "bad",
                    {
                        "name": "example.py",
                        "path": 123,
                        "html_url": "https://example.test/code",
                        "text_matches": [
                            "bad-match",
                            {"fragment": "def main(): pass"},
                        ],
                        "repository": SimpleNamespace(full_name="bad"),
                    },
                    {
                        "name": 99,
                        "repository": {
                            "full_name": "owner/repo",
                            "stargazers_count": "17",
                        },
                    },
                ]
            }
        ),
    )

    results = await research.ResearchEngine().search_code("cache invalidation")

    assert results == [
        research.CodeSearchResult(
            repo="owner/repo",
            path="",
            url="",
            snippet="",
            language="",
            stars=17,
        ),
        research.CodeSearchResult(
            repo="",
            path="",
            url="https://example.test/code",
            snippet="def main(): pass",
            language="py",
            stars=0,
        ),
    ]


@pytest.mark.asyncio
async def test_library_metadata_tolerates_malformed_success_payloads(
    monkeypatch,
) -> None:
    engine = research.ResearchEngine()

    info = research.LibraryInfo(name="pkg")
    monkeypatch.setattr(
        research,
        "_http_client",
        lambda: _ResearchClient(payload={"info": ["bad"]}),
    )
    await engine._fetch_pypi("pkg", info)

    monkeypatch.setattr(
        research,
        "_http_client",
        lambda: _ResearchClient(
            payload={
                "version": 123,
                "description": ["bad"],
                "homepage": None,
                "license": {"type": "MIT"},
            }
        ),
    )
    await engine._fetch_npm("pkg", info)

    monkeypatch.setattr(
        research,
        "_http_client",
        lambda: _ResearchClient(
            payload={
                "crate": {
                    "max_version": 42,
                    "newest_version": "1.2.3",
                    "description": False,
                    "repository": "https://example.test/pkg",
                },
                "versions": ["bad", {"license": 7}],
            }
        ),
    )
    await engine._fetch_crates("pkg", info)

    assert info.latest_version == "1.2.3"
    assert info.description is None
    assert info.homepage == "https://example.test/pkg"
    assert info.license is None


@pytest.mark.asyncio
async def test_osv_lookup_skips_malformed_vulnerability_entries(monkeypatch) -> None:
    engine = research.ResearchEngine()
    info = research.LibraryInfo(name="pkg")
    monkeypatch.setattr(
        research,
        "_http_client",
        lambda: _ResearchClient(
            payload={
                "vulns": [
                    "bad",
                    {
                        "id": 42,
                        "summary": None,
                        "severity": ["bad"],
                        "database_specific": {"severity": 9},
                        "affected": [
                            {
                                "ranges": [
                                    {
                                        "events": [
                                            "bad",
                                            {"fixed": 7},
                                            {"fixed": "1.2.4"},
                                        ]
                                    }
                                ]
                            }
                        ],
                    },
                ]
            }
        ),
    )

    await engine._check_osv_vulns("pkg", "PyPI", info)

    assert info.vulnerabilities == [
        {"id": "", "summary": "", "severity": "unknown", "fixed": "1.2.4"}
    ]
