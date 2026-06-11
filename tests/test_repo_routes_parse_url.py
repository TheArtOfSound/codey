from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from codey.saas.api.repo_routes import _parse_github_url


def test_parse_github_url_strips_query_and_fragment() -> None:
    assert (
        _parse_github_url("https://github.com/openai/openai-python?tab=readme#top")
        == "openai/openai-python"
    )


def test_parse_github_url_accepts_ssh_clone_urls() -> None:
    assert _parse_github_url("git@github.com:openai/openai-python.git") == "openai/openai-python"


def test_parse_github_url_accepts_case_insensitive_ssh_clone_hosts() -> None:
    assert _parse_github_url("git@GitHub.com:openai/openai-python.git") == "openai/openai-python"


def test_parse_github_url_accepts_ssh_scheme_clone_urls() -> None:
    assert (
        _parse_github_url("ssh://git@github.com/openai/openai-python.git")
        == "openai/openai-python"
    )


def test_parse_github_url_strips_git_suffix_before_extra_path_segments() -> None:
    assert (
        _parse_github_url("https://github.com/openai/openai-python.git/tree/main")
        == "openai/openai-python"
    )


def test_parse_github_url_accepts_case_insensitive_https_hosts() -> None:
    assert (
        _parse_github_url("https://GitHub.com/openai/openai-python")
        == "openai/openai-python"
    )


def test_parse_github_url_accepts_case_insensitive_https_scheme() -> None:
    assert (
        _parse_github_url("HTTPS://github.com/openai/openai-python")
        == "openai/openai-python"
    )


def test_parse_github_url_accepts_www_github_hosts() -> None:
    assert (
        _parse_github_url("https://www.github.com/openai/openai-python")
        == "openai/openai-python"
    )


def test_parse_github_url_accepts_dotted_and_underscored_repo_names() -> None:
    assert _parse_github_url("openai/openai_python.js") == "openai/openai_python.js"


def test_parse_github_url_rejects_non_github_scp_style_urls() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_github_url("git@gitlab.com:openai/openai-python.git")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


def test_parse_github_url_rejects_plain_http_urls() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_github_url("http://github.com/openai/openai-python")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.parametrize("url", [None, {"url": "https://github.com/openai/openai-python"}])
def test_parse_github_url_rejects_non_string_values(url) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_github_url(url)  # type: ignore[arg-type]

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com:not-a-port/openai/openai-python",
        "https://github.com:0/openai/openai-python",
        "ssh://git@github.com:0/openai/openai-python.git",
    ],
)
def test_parse_github_url_rejects_invalid_ports(url: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_github_url(url)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


def test_parse_github_url_rejects_http_userinfo() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_github_url("https://token@github.com/openai/openai-python")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


def test_parse_github_url_rejects_ssh_passwords() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_github_url("ssh://git:secret@github.com/openai/openai-python.git")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.parametrize(
    "url",
    [
        "ssh://root@github.com/openai/openai-python.git",
        "ssh://github.com/openai/openai-python.git",
    ],
)
def test_parse_github_url_rejects_ssh_urls_without_git_user(url: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_github_url(url)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


def test_parse_github_url_rejects_control_characters() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_github_url("https://github.com/openai/openai-python\nSet-Cookie: bad=1")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/open ai/openai-python",
        "https://github.com/openai/openai python",
        "https://github.com/openai/..",
        "-openai/openai-python",
        "openai-/openai-python",
    ],
)
def test_parse_github_url_rejects_invalid_owner_repo_segments(url: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_github_url(url)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
