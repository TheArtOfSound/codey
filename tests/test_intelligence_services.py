from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from codey.saas.intelligence import services


class _SemgrepProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return (
            b'{"results": [{"check_id": "python.print", "extra": '
            b'{"severity": "INFO", "message": "print call"}, "start": {"line": 1}}]}',
            b"",
        )


class _MixedSemgrepProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return (
            b'{"results": ['
            b'{"extra": {"severity": "ERROR", "message": "missing rule"}},'
            b'{"check_id": "python.print", "extra": "bad", "start": "bad"},'
            b'42'
            b"]}",
            b"",
        )


class _FailingDrainProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.drain_attempted = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.killed:
            self.drain_attempted = True
            raise RuntimeError(
                "drain failed https://user:secret@example.test/log?api_key=svc-secret"
            )
        await asyncio.sleep(3600)
        return b"", b""

    def kill(self) -> None:
        self.killed = True


class _HangingDrainProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.drain_attempted = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.drain_attempted = True
        await asyncio.sleep(3600)
        return b"", b""

    def kill(self) -> None:
        self.killed = True


def test_redact_service_error_removes_common_secret_shapes() -> None:
    message = services._redact_service_error(
        "provider failed https://user:secret@example.test/search?api_key=svc-secret&client_secret=client123 "
        "access_token=abc123 auth_token=auth123 refresh_token=refresh123 "
        "password=pw123 for user@example.com authorization=Bearer svc-auth"
    )

    assert "user@example.com" not in message
    assert "user:secret" not in message
    assert "secret@example.test" not in message
    assert "svc-secret" not in message
    assert "client123" not in message
    assert "abc123" not in message
    assert "auth123" not in message
    assert "refresh123" not in message
    assert "pw123" not in message
    assert "svc-auth" not in message
    assert "***@example.com" in message
    assert "https://***@example.test/search?api_key=***&client_secret=***" in message
    assert "access_token=***" in message
    assert "auth_token=***" in message
    assert "refresh_token=***" in message
    assert "password=***" in message
    assert "authorization=Bearer ***" in message


def test_coerce_non_empty_service_secret_rejects_internal_whitespace(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SERVICE_API_KEY", " service-key ")
    assert services._coerce_non_empty_service_secret("SERVICE_API_KEY") == "service-key"

    monkeypatch.setenv("SERVICE_API_KEY", "service key")
    assert services._coerce_non_empty_service_secret("SERVICE_API_KEY") is None


def test_coerce_service_webhook_url_strips_fragments(monkeypatch) -> None:
    monkeypatch.setenv(
        "DISCORD_WEBHOOK_URL",
        " https://hooks.example.test/webhook?token=abc#secret ",
    )

    assert (
        services._coerce_service_webhook_url("DISCORD_WEBHOOK_URL")
        == "https://hooks.example.test/webhook?token=abc"
    )


def test_coerce_service_webhook_url_rejects_internal_whitespace(monkeypatch) -> None:
    monkeypatch.setenv(
        "DISCORD_WEBHOOK_URL",
        "https://hooks.example.test/webhook bad",
    )

    assert services._coerce_service_webhook_url("DISCORD_WEBHOOK_URL") is None


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.test/webhook",
        "https://user:pass@hooks.example.test/webhook",
        "https://hooks.example.test:not-a-port/webhook",
        "https://hooks.example.test:0/webhook",
        "https://hooks.example.test/webhook\nX-Bad: 1",
        "https://localhost/webhook",
        "https://service.localhost/webhook",
        "https://127.0.0.1/webhook",
        "https://2130706433/webhook",
        "https://0x7f000001/webhook",
        "https://0177.0.0.1/webhook",
        "https://10.0.0.5/webhook",
        "https://3232235786/webhook",
        "https://0300.0250.0001.0012/webhook",
        "https://169.254.169.254/latest/meta-data",
        "https://2852039166/latest/meta-data",
        "https://0xa9fea9fe/latest/meta-data",
        "https://[::1]/webhook",
        "hooks.example.test/webhook",
    ],
)
def test_coerce_service_webhook_url_rejects_unsafe_urls(
    monkeypatch,
    url: str,
) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", url)

    assert services._coerce_service_webhook_url("DISCORD_WEBHOOK_URL") is None


@pytest.mark.asyncio
async def test_notification_helpers_reject_unsafe_webhooks_without_http(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://2130706433/webhook")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://user:pass@example.test/hook")

    class _UnexpectedHttpClient:
        async def post(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _UnexpectedHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        discord = await svc.notify_discord("hello")
        slack = await svc.notify_slack("hello")
    finally:
        await svc.close()

    assert discord is False
    assert slack is False


@pytest.mark.asyncio
async def test_intelligence_services_close_discards_client_on_close_failure() -> None:
    class _FailingHttpClient:
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    svc = services.IntelligenceServices()
    svc._http_client = _FailingHttpClient()

    with pytest.raises(RuntimeError, match="close failed"):
        await svc.close()

    assert svc._http_client is None


def test_available_providers_ignores_whitespace_only_keys(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "   ")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    available = services.IntelligenceServices.available_providers()

    assert "groq" not in available


def test_available_providers_ignores_control_character_keys(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "groq\tbad")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    available = services.IntelligenceServices.available_providers()

    assert "groq" not in available


def test_normalize_semgrep_findings_coerces_output_fields() -> None:
    result = services._normalize_semgrep_findings(
        [
            {
                "check_id": " python.print ",
                "extra": {"severity": ["bad"], "message": {"bad": "shape"}},
                "start": {"line": "7"},
            },
            {
                "check_id": "python.valid",
                "extra": {"severity": " WARNING ", "message": " be careful "},
                "start": {"line": 3},
            },
        ]
    )

    assert result == [
        {
            "rule": "python.print",
            "severity": "unknown",
            "message": "",
            "line": None,
        },
        {
            "rule": "python.valid",
            "severity": "WARNING",
            "message": "be careful",
            "line": 3,
        },
    ]


def test_coerce_llm_content_accepts_only_chat_message_text() -> None:
    assert (
        services._coerce_llm_content(
            {"choices": [{"message": {"content": "assistant text"}}]}
        )
        == "assistant text"
    )
    assert (
        services._coerce_llm_content(
            {"choices": [{"message": {"content": {"bad": "shape"}}}]}
        )
        is None
    )
    assert services._coerce_llm_content({"choices": [42]}) is None
    assert services._coerce_llm_content({"choices": []}) is None


@pytest.mark.asyncio
async def test_search_perplexity_ignores_malformed_content_payloads(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "perplexity-key")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _MalformedPayloadHttpClient:
        async def post(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "choices": [{"message": {"content": ["bad", "shape"]}}]
                },
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _MalformedPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        result = await svc.search_perplexity("latest Codey health")
    finally:
        await svc.close()

    assert result is None


@pytest.mark.asyncio
async def test_semgrep_scan_uses_async_subprocess(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    scan_file_modes: list[int] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append(tuple(args))
        scan_file_modes.append(Path(args[-1]).stat().st_mode & 0o777)
        return _SemgrepProcess()

    monkeypatch.setattr(services.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await services.IntelligenceServices().semgrep_scan("print('ok')", "python")

    assert calls
    assert calls[0][:5] == ("semgrep", "--config", "auto", "--json", "--quiet")
    assert "--metrics=off" in calls[0]
    assert "--disable-version-check" in calls[0]
    assert scan_file_modes[0] & 0o077 == 0
    assert result == [
        {
            "rule": "python.print",
            "severity": "INFO",
            "message": "print call",
            "line": 1,
        }
    ]


@pytest.mark.asyncio
async def test_semgrep_scan_skips_malformed_findings(monkeypatch) -> None:
    async def fake_create_subprocess_exec(*args, **kwargs):
        return _MixedSemgrepProcess()

    monkeypatch.setattr(services.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await services.IntelligenceServices().semgrep_scan("print('ok')", "python")

    assert result == [
        {
            "rule": "python.print",
            "severity": "unknown",
            "message": "",
            "line": None,
        }
    ]


@pytest.mark.asyncio
async def test_semgrep_scan_defaults_malformed_language_to_python(monkeypatch) -> None:
    scan_suffixes: list[str] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        scan_suffixes.append(Path(args[-1]).suffix)
        return _SemgrepProcess()

    monkeypatch.setattr(services.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await services.IntelligenceServices().semgrep_scan(
        "print('ok')",
        {"language": "python"},  # type: ignore[arg-type]
    )

    assert scan_suffixes == [".py"]
    assert result[0]["rule"] == "python.print"


@pytest.mark.asyncio
async def test_semgrep_scan_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    async def fake_create_subprocess_exec(*args, **kwargs):
        raise RuntimeError(
            "semgrep failed "
            "https://user:secret@example.test/scan?api_key=svc-secret "
            "access_token=abc123 for user@example.com"
        )

    monkeypatch.setattr(
        services.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.services")

    result = await services.IntelligenceServices().semgrep_scan("print('ok')", "python")

    assert result == []
    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text.lower()
    assert "svc-secret" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/scan?api_key=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_semgrep_scan_timeout_cleanup_is_best_effort(
    monkeypatch,
    caplog,
) -> None:
    process = _FailingDrainProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    wait_for_calls = 0

    async def fake_wait_for(awaitable, timeout):
        nonlocal wait_for_calls
        wait_for_calls += 1
        if wait_for_calls == 1:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise asyncio.TimeoutError()
        return await awaitable

    monkeypatch.setattr(services.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(services.asyncio, "wait_for", fake_wait_for)
    caplog.set_level(logging.WARNING, logger="codey.saas.intelligence.services")

    result = await services.IntelligenceServices().semgrep_scan("print('ok')", "python")

    assert result == []
    assert process.killed is True
    assert process.drain_attempted is True
    assert "secret" not in caplog.text.lower()
    assert "svc-secret" not in caplog.text
    assert "https://***@example.test/log?api_key=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_terminate_service_process_bounds_drain_wait(monkeypatch) -> None:
    process = _HangingDrainProcess()
    monkeypatch.setattr(services, "_SERVICE_DRAIN_TIMEOUT_SECONDS", 0.01)

    await services._terminate_service_process(process, "Semgrep")

    assert process.killed is True
    assert process.drain_attempted is True


@pytest.mark.asyncio
async def test_llm_complete_skips_http_when_cloudflare_account_id_is_whitespace(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CLOUDFLARE_API_KEY", " cf-key ")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "   ")

    class _UnexpectedHttpClient:
        async def post(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(services.httpx, "AsyncClient", lambda timeout: _UnexpectedHttpClient())

    svc = services.IntelligenceServices()
    try:
        result = await svc.llm_complete(
            "cloudflare",
            "model-name",
            [{"role": "user", "content": "hello"}],
        )
    finally:
        await svc.close()

    assert result is None


@pytest.mark.asyncio
async def test_llm_complete_ignores_malformed_content_payloads(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _MalformedPayloadHttpClient:
        async def post(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "choices": [{"message": {"content": {"bad": "shape"}}}]
                },
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _MalformedPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        result = await svc.llm_complete(
            "groq",
            "model-name",
            [{"role": "user", "content": "hello"}],
        )
    finally:
        await svc.close()

    assert result is None


@pytest.mark.asyncio
async def test_llm_complete_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _FailingHttpClient:
        async def post(self, *args, **kwargs):
            raise RuntimeError(
                "llm failed "
                "https://user:secret@example.test/chat?token=svc-token "
                "access_token=abc123 for user@example.com"
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _FailingHttpClient(),
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.services")

    svc = services.IntelligenceServices()
    try:
        result = await svc.llm_complete(
            "groq",
            "model/https://user:secret@example.test/model?token=model-token",
            [{"role": "user", "content": "hello"}],
        )
    finally:
        await svc.close()

    assert result is None
    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text
    assert "svc-token" not in caplog.text
    assert "model-token" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/chat?token=***" in caplog.text
    assert "https://***@example.test/model?token=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_github_search_helpers_ignore_whitespace_tokens(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "   ")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "   ")

    requests: list[dict[str, object]] = []

    class _CaptureHttpClient:
        async def get(self, url: str, **kwargs):
            requests.append({"url": url, "headers": kwargs.get("headers", {})})
            return SimpleNamespace(status_code=200, json=lambda: {"items": []})

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(services.httpx, "AsyncClient", lambda timeout: _CaptureHttpClient())

    svc = services.IntelligenceServices()
    try:
        code_results = await svc.search_github_code("cache invalidation", language="python")
        repo_results = await svc.search_github_repos("cache invalidation")
    finally:
        await svc.close()

    assert code_results == []
    assert repo_results == []
    assert len(requests) == 2
    assert all("Authorization" not in request["headers"] for request in requests)


@pytest.mark.asyncio
async def test_github_search_helpers_filter_malformed_items(monkeypatch) -> None:
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _MixedPayloadHttpClient:
        async def get(self, url: str, **kwargs):
            if url.endswith("/search/code"):
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {
                        "items": [
                            "bad",
                            {"path": "missing repo", "html_url": "https://example.com"},
                            {
                                "repository": {"full_name": "owner/repo"},
                                "path": "src/app.py",
                                "html_url": "https://github.com/owner/repo/src/app.py",
                            },
                        ]
                    },
                )
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "items": [
                        "bad",
                        {"full_name": "missing-url"},
                        {
                            "full_name": "owner/repo",
                            "description": "example",
                            "stargazers_count": 10,
                            "html_url": "https://github.com/owner/repo",
                        },
                    ]
                },
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _MixedPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        code_results = await svc.search_github_code("cache invalidation")
        repo_results = await svc.search_github_repos("cache invalidation")
    finally:
        await svc.close()

    assert code_results == [
        {
            "repo": "owner/repo",
            "path": "src/app.py",
            "url": "https://github.com/owner/repo/src/app.py",
        }
    ]
    assert repo_results == [
        {
            "full_name": "owner/repo",
            "description": "example",
            "stars": 10,
            "url": "https://github.com/owner/repo",
        }
    ]


@pytest.mark.asyncio
async def test_github_search_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _FailingHttpClient:
        async def get(self, *args, **kwargs):
            raise RuntimeError(
                "github failed "
                "https://user:secret@example.test/search?token=svc-token "
                "access_token=abc123 for user@example.com"
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _FailingHttpClient(),
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.services")

    svc = services.IntelligenceServices()
    try:
        result = await svc.search_github_code(
            "user@example.com "
            "https://user:secret@example.test/search?token=query-token",
            language="python",
        )
    finally:
        await svc.close()

    assert result == []
    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text
    assert "svc-token" not in caplog.text
    assert "query-token" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/search?token=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_package_lookup_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _FailingHttpClient:
        async def get(self, *args, **kwargs):
            raise RuntimeError(
                "registry failed "
                "https://user:secret@example.test/package?token=svc-token "
                "access_token=abc123 for user@example.com"
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _FailingHttpClient(),
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.services")

    svc = services.IntelligenceServices()
    try:
        result = await svc.get_pypi_info(
            "pkg/https://user:secret@example.test/pkg?token=query-token"
        )
    finally:
        await svc.close()

    assert result is None
    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text
    assert "svc-token" not in caplog.text
    assert "query-token" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/package?token=***" in caplog.text
    assert "https://***@example.test/pkg?token=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_pypi_info_uses_project_urls_and_rejects_malformed_info(
    monkeypatch,
) -> None:
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    payloads = [
        {
            "info": {
                "version": "2.31.0",
                "summary": "HTTP for Humans",
                "home_page": "",
                "project_urls": {"Homepage": "https://requests.readthedocs.io"},
            }
        },
        {"info": "bad"},
    ]

    class _PypiPayloadHttpClient:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, json=lambda: payloads.pop(0))

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _PypiPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        valid = await svc.get_pypi_info("requests")
        malformed = await svc.get_pypi_info("requests")
    finally:
        await svc.close()

    assert valid == {
        "name": "requests",
        "version": "2.31.0",
        "summary": "HTTP for Humans",
        "home_page": "https://requests.readthedocs.io",
        "requires_python": None,
        "license": None,
    }
    assert malformed is None


@pytest.mark.asyncio
async def test_npm_info_accepts_string_or_dict_repository_payloads(monkeypatch) -> None:
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    payloads = [
        {
            "version": "1.0.0",
            "description": "string repository",
            "repository": "github:example/pkg",
        },
        {
            "version": "1.0.1",
            "description": "dict repository",
            "repository": {"url": "git+https://github.com/example/pkg.git"},
        },
    ]

    class _NpmPayloadHttpClient:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, json=lambda: payloads.pop(0))

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _NpmPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        string_repo = await svc.get_npm_info("example-pkg")
        dict_repo = await svc.get_npm_info("example-pkg")
    finally:
        await svc.close()

    assert string_repo == {
        "name": "example-pkg",
        "version": "1.0.0",
        "description": "string repository",
        "homepage": None,
        "repository": "github:example/pkg",
    }
    assert dict_repo == {
        "name": "example-pkg",
        "version": "1.0.1",
        "description": "dict repository",
        "homepage": None,
        "repository": "git+https://github.com/example/pkg.git",
    }


@pytest.mark.asyncio
async def test_crates_info_rejects_malformed_crate_payload(monkeypatch) -> None:
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    payloads = [
        {"crate": "bad"},
        {
            "crate": {
                "newest_version": "1.0.0",
                "description": "Rust package",
                "repository": "https://github.com/example/crate",
                "downloads": 42,
            }
        },
    ]

    class _CratesPayloadHttpClient:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, json=lambda: payloads.pop(0))

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _CratesPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        malformed = await svc.get_crates_info("example-crate")
        valid = await svc.get_crates_info("example-crate")
    finally:
        await svc.close()

    assert malformed is None
    assert valid == {
        "name": "example-crate",
        "version": "1.0.0",
        "description": "Rust package",
        "homepage": None,
        "repository": "https://github.com/example/crate",
        "downloads": 42,
    }


@pytest.mark.asyncio
async def test_maven_info_skips_malformed_docs(monkeypatch) -> None:
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _MavenPayloadHttpClient:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "response": {
                        "docs": [
                            "bad",
                            {
                                "g": "com.example",
                                "a": "lib",
                                "latestVersion": "1.2.3",
                                "timestamp": 12345,
                            },
                        ]
                    }
                },
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _MavenPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        result = await svc.get_maven_info("com.example", "lib")
    finally:
        await svc.close()

    assert result == {
        "group_id": "com.example",
        "artifact_id": "lib",
        "version": "1.2.3",
        "timestamp": 12345,
    }


@pytest.mark.asyncio
async def test_packagist_info_ignores_malformed_versions(monkeypatch) -> None:
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _PackagistPayloadHttpClient:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "package": {
                        "description": "Composer package",
                        "versions": {
                            "dev-main": {"version": "dev-main"},
                            42: {"version": "bad"},
                            "1.2.3": {
                                "version": "1.2.3",
                                "homepage": "https://example.com/pkg",
                            },
                        },
                    }
                },
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _PackagistPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        result = await svc.get_packagist_info("vendor/package")
    finally:
        await svc.close()

    assert result == {
        "name": "vendor/package",
        "version": "1.2.3",
        "description": "Composer package",
        "homepage": "https://example.com/pkg",
    }


@pytest.mark.asyncio
async def test_search_helpers_ignore_whitespace_api_keys(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "   ")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "   ")
    monkeypatch.setenv("EXA_API_KEY", "   ")
    monkeypatch.setenv("BING_SEARCH_API_KEY", "   ")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "   ")

    class _UnexpectedHttpClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def post(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(services.httpx, "AsyncClient", lambda timeout: _UnexpectedHttpClient())

    svc = services.IntelligenceServices()
    try:
        tavily = await svc.search_tavily("semantic cache invalidation")
        brave = await svc.search_brave("semantic cache invalidation")
        exa = await svc.search_exa("semantic cache invalidation")
        bing = await svc.search_bing("semantic cache invalidation")
        perplexity = await svc.search_perplexity("semantic cache invalidation")
    finally:
        await svc.close()

    assert tavily is None
    assert brave is None
    assert exa is None
    assert bing is None
    assert perplexity is None


@pytest.mark.asyncio
async def test_search_helpers_ignore_control_character_api_keys(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily\tbad")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave\nbad")
    monkeypatch.setenv("EXA_API_KEY", "exa\x7fbad")
    monkeypatch.setenv("BING_SEARCH_API_KEY", "bing\tbad")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "perplexity\tbad")

    class _UnexpectedHttpClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def post(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(services.httpx, "AsyncClient", lambda timeout: _UnexpectedHttpClient())

    svc = services.IntelligenceServices()
    try:
        tavily = await svc.search_tavily("semantic cache invalidation")
        brave = await svc.search_brave("semantic cache invalidation")
        exa = await svc.search_exa("semantic cache invalidation")
        bing = await svc.search_bing("semantic cache invalidation")
        perplexity = await svc.search_perplexity("semantic cache invalidation")
    finally:
        await svc.close()

    assert tavily is None
    assert brave is None
    assert exa is None
    assert bing is None
    assert perplexity is None


@pytest.mark.asyncio
async def test_search_tavily_filters_malformed_result_entries(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _MixedPayloadHttpClient:
        async def post(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"results": ["bad", {"title": "ok"}]},
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _MixedPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        result = await svc.search_tavily("semantic cache invalidation")
    finally:
        await svc.close()

    assert result == [{"title": "ok"}]


@pytest.mark.asyncio
async def test_search_web_skips_malformed_provider_results(monkeypatch) -> None:
    svc = services.IntelligenceServices()

    async def malformed_provider(query: str):
        return {"bad": "shape"}

    async def empty_provider(query: str):
        return []

    async def valid_provider(query: str):
        return [{"title": "ok"}]

    monkeypatch.setattr(svc, "search_tavily", malformed_provider)
    monkeypatch.setattr(svc, "search_brave", empty_provider)
    monkeypatch.setattr(svc, "search_exa", valid_provider)
    monkeypatch.setattr(svc, "search_bing", malformed_provider)

    assert await svc.search_web("semantic cache invalidation") == [{"title": "ok"}]


@pytest.mark.asyncio
async def test_search_provider_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")

    class _FailingHttpClient:
        async def post(self, *args, **kwargs):
            raise RuntimeError(
                "provider failed "
                "https://user:secret@example.test/search?api_key=svc-secret "
                "access_token=abc123 for user@example.com"
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _FailingHttpClient(),
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.services")

    svc = services.IntelligenceServices()
    try:
        result = await svc.search_tavily(
            "user@example.com "
            "https://user:secret@example.test/search?token=query-token"
        )
    finally:
        await svc.close()

    assert result is None
    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text
    assert "svc-secret" not in caplog.text
    assert "abc123" not in caplog.text
    assert "query-token" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/search?api_key=***" in caplog.text
    assert "https://***@example.test/search?token=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_notification_helpers_ignore_whitespace_credentials(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "   ")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "   ")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "   ")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "   ")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "   ")

    class _UnexpectedHttpClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def post(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(services.httpx, "AsyncClient", lambda timeout: _UnexpectedHttpClient())

    svc = services.IntelligenceServices()
    try:
        sms = await svc.send_sms_twilio("+15551234567", "hello")
        discord = await svc.notify_discord("hello")
        slack = await svc.notify_slack("hello")
    finally:
        await svc.close()

    assert sms is False
    assert discord is False
    assert slack is False


@pytest.mark.asyncio
async def test_notification_helper_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hooks.example.test/webhook")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _FailingHttpClient:
        async def post(self, *args, **kwargs):
            raise RuntimeError(
                "notify failed "
                "https://user:secret@example.test/webhook?token=svc-token "
                "access_token=abc123 for user@example.com"
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _FailingHttpClient(),
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.services")

    svc = services.IntelligenceServices()
    try:
        result = await svc.notify_discord("hello")
    finally:
        await svc.close()

    assert result is False
    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text
    assert "svc-token" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/webhook?token=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_deployment_helpers_ignore_whitespace_tokens(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL_TOKEN", "   ")
    monkeypatch.setenv("RAILWAY_TOKEN", "   ")

    class _UnexpectedHttpClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def post(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(services.httpx, "AsyncClient", lambda timeout: _UnexpectedHttpClient())

    svc = services.IntelligenceServices()
    try:
        vercel = await svc.vercel_get_deployments()
        railway = await svc.railway_get_services("project-123")
    finally:
        await svc.close()

    assert vercel == []
    assert railway == []


@pytest.mark.asyncio
async def test_deployment_helper_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("VERCEL_TOKEN", "vercel-token")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _FailingHttpClient:
        async def get(self, *args, **kwargs):
            raise RuntimeError(
                "deploy fetch failed "
                "https://user:secret@example.test/deployments?token=svc-token "
                "access_token=abc123 for user@example.com"
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _FailingHttpClient(),
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.services")

    svc = services.IntelligenceServices()
    try:
        result = await svc.vercel_get_deployments()
    finally:
        await svc.close()

    assert result == []
    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text
    assert "svc-token" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/deployments?token=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_deployment_helpers_filter_malformed_list_payloads(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL_TOKEN", "vercel-token")
    monkeypatch.setenv("RAILWAY_TOKEN", "railway-token")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _MixedPayloadHttpClient:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "deployments": [
                        "bad",
                        {"name": "missing id"},
                        {"uid": "dep_123", "name": "api", "state": "READY"},
                    ]
                },
            )

        async def post(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "data": {
                        "project": {
                            "services": {
                                "edges": [
                                    "bad",
                                    {"node": "bad"},
                                    {"node": {"id": "svc_123", "name": "api"}},
                                ]
                            }
                        }
                    }
                },
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _MixedPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        vercel = await svc.vercel_get_deployments()
        railway = await svc.railway_get_services("project-123")
    finally:
        await svc.close()

    assert vercel == [
        {
            "id": "dep_123",
            "name": "api",
            "state": "READY",
            "url": None,
            "created": None,
        }
    ]
    assert railway == [{"id": "svc_123", "name": "api"}]


@pytest.mark.asyncio
async def test_monitoring_helpers_ignore_whitespace_api_keys(monkeypatch) -> None:
    monkeypatch.setenv("BETTERSTACK_API_KEY", "   ")
    monkeypatch.setenv("UPTIMEROBOT_API_KEY", "   ")

    class _UnexpectedHttpClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def post(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(services.httpx, "AsyncClient", lambda timeout: _UnexpectedHttpClient())

    svc = services.IntelligenceServices()
    try:
        betterstack_create = await svc.betterstack_create_monitor("https://example.com")
        betterstack_list = await svc.betterstack_get_monitors()
        uptimerobot_list = await svc.uptimerobot_get_monitors()
        uptimerobot_create = await svc.uptimerobot_create_monitor("https://example.com")
    finally:
        await svc.close()

    assert betterstack_create is None
    assert betterstack_list == []
    assert uptimerobot_list == []
    assert uptimerobot_create is None


@pytest.mark.asyncio
async def test_monitoring_helpers_filter_malformed_payloads(monkeypatch) -> None:
    monkeypatch.setenv("BETTERSTACK_API_KEY", "betterstack-key")
    monkeypatch.setenv("UPTIMEROBOT_API_KEY", "uptimerobot-key")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _MixedPayloadHttpClient:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "data": [
                        "bad",
                        {"id": "missing-attributes"},
                        {"id": "bad-attributes", "attributes": "bad"},
                        {
                            "id": "mon_123",
                            "attributes": {
                                "url": "https://example.com",
                                "status": "up",
                            },
                        },
                    ]
                },
            )

        async def post(self, url: str, **kwargs):
            if "getMonitors" in url:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {
                        "monitors": [
                            "bad",
                            {"id": 1, "friendly_name": "missing url"},
                            {
                                "id": 2,
                                "friendly_name": "api",
                                "url": "https://api.example.com",
                                "status": 2,
                            },
                        ]
                    },
                )
            if "newMonitor" in url:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"stat": "ok", "monitor": {"id": 2}},
                )
            return SimpleNamespace(
                status_code=201,
                json=lambda: {
                    "data": {
                        "attributes": {
                            "url": "https://example.com",
                            "status": "up",
                        }
                    }
                },
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _MixedPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        betterstack_create = await svc.betterstack_create_monitor("https://example.com")
        betterstack_list = await svc.betterstack_get_monitors()
        uptimerobot_list = await svc.uptimerobot_get_monitors()
        uptimerobot_create = await svc.uptimerobot_create_monitor("https://example.com")
    finally:
        await svc.close()

    assert betterstack_create == {"url": "https://example.com", "status": "up"}
    assert betterstack_list == [
        {
            "id": "mon_123",
            "url": "https://example.com",
            "status": "up",
            "last_checked": None,
        }
    ]
    assert uptimerobot_list == [
        {
            "id": 2,
            "name": "api",
            "url": "https://api.example.com",
            "status": 2,
        }
    ]
    assert uptimerobot_create == {"id": 2}


@pytest.mark.asyncio
async def test_security_helpers_ignore_whitespace_keys(monkeypatch) -> None:
    monkeypatch.setenv("NVD_API_KEY", "   ")
    monkeypatch.setenv("SNYK_API_KEY", "   ")

    requests: list[dict[str, object]] = []

    class _CaptureHttpClient:
        async def get(self, url: str, **kwargs):
            requests.append({"url": url, "headers": kwargs.get("headers", {})})
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"vulnerabilities": [{"cve": {"id": "CVE-2024-1234"}}]},
            )

        async def post(self, *args, **kwargs):
            raise AssertionError("Snyk HTTP call should not be made for blank keys")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(services.httpx, "AsyncClient", lambda timeout: _CaptureHttpClient())

    svc = services.IntelligenceServices()
    try:
        nvd = await svc.check_nvd("CVE-2024-1234")
        snyk = await svc.check_snyk([{"name": "requests", "version": "2.31.0"}])
    finally:
        await svc.close()

    assert nvd == {"id": "CVE-2024-1234"}
    assert snyk == []
    assert len(requests) == 1
    assert "apiKey" not in requests[0]["headers"]


@pytest.mark.asyncio
async def test_security_helpers_filter_malformed_vulnerability_payloads(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SNYK_API_KEY", "snyk-key")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _MixedPayloadHttpClient:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "vulnerabilities": ["bad", {"cve": {"id": "CVE-2024-1234"}}]
                },
            )

        async def post(self, url: str, **kwargs):
            if "osv.dev" in url:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"vulns": ["bad", {"id": "OSV-2024-1234"}]},
                )
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "issues": {
                        "vulnerabilities": [
                            "bad",
                            {
                                "package": "requests",
                                "severity": "high",
                                "title": "SNYK-2024-1234",
                            },
                        ]
                    }
                },
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _MixedPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        osv = await svc.check_osv("requests", "2.31.0")
        nvd = await svc.check_nvd("CVE-2024-1234")
        snyk = await svc.check_snyk([{"name": "requests", "version": "2.31.0"}])
    finally:
        await svc.close()

    assert osv == [{"id": "OSV-2024-1234"}]
    assert nvd == {"id": "CVE-2024-1234"}
    assert snyk == [
        {
            "package": "requests",
            "severity": "high",
            "title": "SNYK-2024-1234",
            "fix": "No fix",
        }
    ]


@pytest.mark.asyncio
async def test_check_package_security_ignores_non_list_child_results(
    monkeypatch,
) -> None:
    svc = services.IntelligenceServices()

    async def malformed_osv(package: str, version: str, ecosystem: str):
        return {"bad": "shape"}

    async def malformed_snyk(packages: list[dict]):
        return "bad"

    monkeypatch.setattr(svc, "check_osv", malformed_osv)
    monkeypatch.setattr(svc, "check_snyk", malformed_snyk)

    result = await svc.check_package_security("requests", "2.31.0", "python")

    assert result["vulnerabilities"] == 0
    assert result["details"] == []


@pytest.mark.asyncio
async def test_security_check_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("SNYK_API_KEY", "snyk-key")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _FailingHttpClient:
        async def get(self, *args, **kwargs):
            raise RuntimeError(
                "security failed "
                "https://user:secret@example.test/vuln?token=svc-token "
                "access_token=abc123 for user@example.com"
            )

        async def post(self, *args, **kwargs):
            raise RuntimeError(
                "security failed "
                "https://user:secret@example.test/vuln?token=svc-token "
                "authorization=abc123 for user@example.com"
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _FailingHttpClient(),
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.services")

    svc = services.IntelligenceServices()
    try:
        osv = await svc.check_osv(
            "pkg/https://user:secret@example.test/pkg?token=package-token",
            "1.0.0+https://user:secret@example.test/ver?token=version-token",
        )
        nvd = await svc.check_nvd(
            "CVE-2024-1234/https://user:secret@example.test/cve?token=cve-token"
        )
        snyk = await svc.check_snyk([{"name": "requests", "version": "2.31.0"}])
    finally:
        await svc.close()

    assert osv == []
    assert nvd is None
    assert snyk == []
    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text
    assert "svc-token" not in caplog.text
    assert "package-token" not in caplog.text
    assert "version-token" not in caplog.text
    assert "cve-token" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/vuln?token=***" in caplog.text
    assert "https://***@example.test/pkg?token=***" in caplog.text
    assert "https://***@example.test/ver?token=***" in caplog.text
    assert "https://***@example.test/cve?token=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "authorization=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_code_quality_helpers_ignore_whitespace_tokens(monkeypatch) -> None:
    monkeypatch.setenv("SONARCLOUD_TOKEN", "   ")
    monkeypatch.setenv("AIKIDO_API_KEY", "   ")
    monkeypatch.setenv("DEEPSOURCE_TOKEN", "   ")

    class _UnexpectedHttpClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def post(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(services.httpx, "AsyncClient", lambda timeout: _UnexpectedHttpClient())

    svc = services.IntelligenceServices()
    try:
        sonarcloud = await svc.check_sonarcloud("codey/project")
        aikido = await svc.check_aikido("https://github.com/example/repo")
        deepsource = await svc.check_deepsource("example/repo")
    finally:
        await svc.close()

    assert sonarcloud is None
    assert aikido is None
    assert deepsource is None


@pytest.mark.asyncio
async def test_code_quality_helpers_filter_malformed_payloads(monkeypatch) -> None:
    monkeypatch.setenv("SONARCLOUD_TOKEN", "sonar-token")
    monkeypatch.setenv("AIKIDO_API_KEY", "aikido-key")
    monkeypatch.setenv("DEEPSOURCE_TOKEN", "deepsource-token")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _MixedPayloadHttpClient:
        async def get(self, url: str, **kwargs):
            if "sonarcloud" in url:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {
                        "component": {
                            "measures": [
                                "bad",
                                {"metric": "missing value"},
                                {"metric": "bugs", "value": "0"},
                            ]
                        }
                    },
                )
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"issues": ["bad", {"id": "AIK-1"}]},
            )

        async def post(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "data": {"repository": {"activeIssueCount": 1}}
                },
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _MixedPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        sonarcloud = await svc.check_sonarcloud("codey/project")
        aikido = await svc.check_aikido("https://github.com/example/repo")
        deepsource = await svc.check_deepsource("example/repo")
    finally:
        await svc.close()

    assert sonarcloud == {"bugs": "0"}
    assert aikido == [{"id": "AIK-1"}]
    assert deepsource == {"activeIssueCount": 1}


@pytest.mark.asyncio
async def test_code_quality_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("SONARCLOUD_TOKEN", "sonar-token")
    monkeypatch.setenv("AIKIDO_API_KEY", "aikido-key")
    monkeypatch.setenv("DEEPSOURCE_TOKEN", "deepsource-token")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _FailingHttpClient:
        async def get(self, *args, **kwargs):
            raise RuntimeError(
                "quality failed "
                "https://user:secret@example.test/quality?token=svc-token "
                "access_token=abc123 for user@example.com"
            )

        async def post(self, *args, **kwargs):
            raise RuntimeError(
                "quality failed "
                "https://user:secret@example.test/graphql?token=svc-token "
                "authorization=abc123 for user@example.com"
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _FailingHttpClient(),
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.services")

    svc = services.IntelligenceServices()
    try:
        sonarcloud = await svc.check_sonarcloud(
            "project/https://user:secret@example.test/project?token=project-token"
        )
        aikido = await svc.check_aikido(
            "https://user:secret@example.test/repo?token=repo-token"
        )
        deepsource = await svc.check_deepsource(
            "repo/https://user:secret@example.test/deepsource?token=repo-token"
        )
    finally:
        await svc.close()

    assert sonarcloud is None
    assert aikido is None
    assert deepsource is None
    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text
    assert "svc-token" not in caplog.text
    assert "project-token" not in caplog.text
    assert "repo-token" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/quality?token=***" in caplog.text
    assert "https://***@example.test/graphql?token=***" in caplog.text
    assert "https://***@example.test/project?token=***" in caplog.text
    assert "https://***@example.test/repo?token=***" in caplog.text
    assert "https://***@example.test/deepsource?token=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "authorization=***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_workflow_helpers_ignore_whitespace_keys(monkeypatch) -> None:
    monkeypatch.setenv("LIBRARIES_IO_API_KEY", "   ")
    monkeypatch.setenv("LINEAR_API_KEY", "   ")

    class _UnexpectedHttpClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def post(self, *args, **kwargs):
            raise AssertionError("http client should not be called")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(services.httpx, "AsyncClient", lambda timeout: _UnexpectedHttpClient())

    svc = services.IntelligenceServices()
    try:
        libraries = await svc.fetch_libraries_io("requests")
        linear = await svc.linear_get_issues()
    finally:
        await svc.close()

    assert libraries is None
    assert linear == []


@pytest.mark.asyncio
async def test_documentation_helpers_filter_malformed_payloads(monkeypatch) -> None:
    monkeypatch.setenv("LIBRARIES_IO_API_KEY", "libraries-key")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _MixedPayloadHttpClient:
        async def get(self, url: str, **kwargs):
            if url.startswith("https://devdocs.io/"):
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: [
                        "bad",
                        {"name": "asyncio"},
                        {"name": ["bad", "shape"]},
                        {"name": "httpx"},
                    ],
                )
            return SimpleNamespace(
                status_code=200,
                json=lambda: ["bad", "shape"],
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _MixedPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        devdocs = await svc.fetch_devdocs("python")
        libraries = await svc.fetch_libraries_io("requests")
    finally:
        await svc.close()

    assert devdocs == "asyncio\nhttpx"
    assert libraries is None


@pytest.mark.asyncio
async def test_documentation_helpers_escape_path_segments(monkeypatch) -> None:
    monkeypatch.setenv("LIBRARIES_IO_API_KEY", "libraries-key")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)
    requested_urls: list[str] = []

    class _CaptureHttpClient:
        async def get(self, url: str, **kwargs):
            requested_urls.append(url)
            return SimpleNamespace(status_code=404, json=lambda: {})

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _CaptureHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        await svc.fetch_devdocs("python/../../?token=secret")
        await svc.fetch_libraries_io(
            "@scope/pkg?token=secret",
            platform="npm/bad",
        )
    finally:
        await svc.close()

    assert requested_urls == [
        "https://devdocs.io/api/entries/python%2F..%2F..%2F%3Ftoken%3Dsecret",
        "https://libraries.io/api/npm%2Fbad/%40scope%2Fpkg%3Ftoken%3Dsecret",
    ]


@pytest.mark.asyncio
async def test_linear_get_issues_filters_malformed_nodes(monkeypatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "linear-key")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _MixedPayloadHttpClient:
        async def post(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "data": {
                        "issues": {
                            "nodes": [
                                "bad",
                                {"title": "missing identifier"},
                                {"identifier": "CODEY-1"},
                                {
                                    "identifier": "CODEY-2",
                                    "title": "Ship safer parser",
                                    "state": {"name": "In Progress"},
                                    "priority": 2,
                                    "assignee": {"name": "Bry"},
                                },
                            ]
                        }
                    }
                },
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _MixedPayloadHttpClient(),
    )

    svc = services.IntelligenceServices()
    try:
        result = await svc.linear_get_issues()
    finally:
        await svc.close()

    assert result == [
        {
            "id": "CODEY-2",
            "title": "Ship safer parser",
            "state": "In Progress",
            "priority": 2,
            "assignee": "Bry",
        }
    ]


@pytest.mark.asyncio
async def test_workflow_helper_failures_are_redacted_without_tracebacks(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("LIBRARIES_IO_API_KEY", "libraries-key")
    monkeypatch.setenv("LINEAR_API_KEY", "linear-key")
    monkeypatch.setattr(services, "_HTTPX_IMPORT_ERROR", None)

    class _FailingHttpClient:
        async def get(self, *args, **kwargs):
            raise RuntimeError(
                "workflow failed "
                "https://user:secret@example.test/docs?token=svc-token "
                "access_token=abc123 for user@example.com"
            )

        async def post(self, *args, **kwargs):
            raise RuntimeError(
                "workflow failed "
                "https://user:secret@example.test/linear?token=svc-token "
                "authorization=abc123 for user@example.com"
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda timeout: _FailingHttpClient(),
    )
    caplog.set_level(logging.DEBUG, logger="codey.saas.intelligence.services")

    svc = services.IntelligenceServices()
    try:
        devdocs = await svc.fetch_devdocs(
            "lib/https://user:secret@example.test/lib?token=library-token"
        )
        libraries = await svc.fetch_libraries_io(
            "pkg/https://user:secret@example.test/pkg?token=package-token",
            platform="platform/https://user:secret@example.test/platform?token=platform-token",
        )
        linear = await svc.linear_get_issues(limit=7)
    finally:
        await svc.close()

    assert devdocs is None
    assert libraries is None
    assert linear == []
    assert "user@example.com" not in caplog.text
    assert "secret" not in caplog.text
    assert "svc-token" not in caplog.text
    assert "library-token" not in caplog.text
    assert "package-token" not in caplog.text
    assert "platform-token" not in caplog.text
    assert "abc123" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.test/docs?token=***" in caplog.text
    assert "https://***@example.test/linear?token=***" in caplog.text
    assert "https://***@example.test/lib?token=***" in caplog.text
    assert "https://***@example.test/pkg?token=***" in caplog.text
    assert "https://***@example.test/platform?token=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "authorization=***" in caplog.text
    assert "Traceback" not in caplog.text
