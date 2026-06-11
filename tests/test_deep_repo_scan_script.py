from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_deep_repo_scan_module():
    path = Path("scripts/deep_repo_scan.py")
    spec = importlib.util.spec_from_file_location("deep_repo_scan", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_deep_repo_scan_import_is_dependency_light() -> None:
    module = _load_deep_repo_scan_module()

    assert module.main is not None


def test_deep_repo_scan_redacts_remote_url_credentials() -> None:
    module = _load_deep_repo_scan_module()

    redacted = module._redact_remote_url(
        "https://user:pass@example.com/repo.git"
        "?client_secret=secret&token=raw#access_token=fragment"
    )

    assert redacted == "https://***@example.com/repo.git?client_secret=***&token=***"
    assert "user:pass" not in redacted
    assert "client_secret=secret" not in redacted
    assert "token=raw" not in redacted
    assert "fragment" not in redacted


def test_deep_repo_scan_redacts_fragment_only_remote_url_secret() -> None:
    module = _load_deep_repo_scan_module()

    redacted = module._redact_remote_url(
        "https://example.com/repo.git#access_token=fragment-secret"
    )

    assert redacted == "https://example.com/repo.git#access_token=***"
    assert "fragment-secret" not in redacted


def test_deep_repo_scan_git_metadata_uses_timeout() -> None:
    module = _load_deep_repo_scan_module()
    observed: dict[str, object] = {}

    class _Result:
        stdout = "main\n"

    def fake_run(*args, **kwargs):
        observed["args"] = args
        observed["timeout"] = kwargs.get("timeout")
        observed["encoding"] = kwargs.get("encoding")
        observed["errors"] = kwargs.get("errors")
        return _Result()

    original_run = module.subprocess.run
    try:
        module.subprocess.run = fake_run

        assert module._run_git(["rev-parse", "--abbrev-ref", "HEAD"], Path(".")) == "main"
    finally:
        module.subprocess.run = original_run

    assert observed["timeout"] == module._GIT_TIMEOUT_SECONDS
    assert observed["encoding"] == "utf-8"
    assert observed["errors"] == "replace"
