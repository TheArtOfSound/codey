from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

import codey.saas.api.github_routes as github_routes


def test_create_pr_request_rejects_blank_title() -> None:
    with pytest.raises(ValidationError):
        github_routes.CreatePRRequest(
            session_id=str(uuid.uuid4()),
            title="   ",
        )


def test_create_pr_request_rejects_blank_session_id() -> None:
    with pytest.raises(ValidationError):
        github_routes.CreatePRRequest(
            session_id="   ",
            title="Fix parser bug",
        )


def test_create_pr_request_strips_branch_fields_and_optional_body() -> None:
    session_id = str(uuid.uuid4())
    request = github_routes.CreatePRRequest(
        session_id=f"  {session_id}  ",
        title="  Fix parser bug  ",
        base_branch="  refs/heads/main  ",
        head_branch="  refs/heads/codey/fix-parser  ",
        body="  Ready for review.  ",
    )

    assert request.session_id == session_id
    assert request.title == "Fix parser bug"
    assert request.base_branch == "main"
    assert request.head_branch == "codey/fix-parser"
    assert request.body == "Ready for review."


def test_fix_issue_request_normalizes_blank_branch_name_to_none() -> None:
    request = github_routes.FixIssueRequest(branch_name="   ")

    assert request.branch_name is None


def test_fix_issue_request_normalizes_branch_ref_prefix() -> None:
    request = github_routes.FixIssueRequest(branch_name=" refs/heads/codey/fix-issue ")

    assert request.branch_name == "codey/fix-issue"


def test_create_pr_request_rejects_invalid_branch_names() -> None:
    with pytest.raises(ValidationError):
        github_routes.CreatePRRequest(
            session_id=str(uuid.uuid4()),
            title="Fix parser bug",
            base_branch="../main",
        )


def test_create_pr_request_rejects_control_character_branch_names() -> None:
    with pytest.raises(ValidationError):
        github_routes.CreatePRRequest(
            session_id=str(uuid.uuid4()),
            title="Fix parser bug",
            base_branch="main\ninjected",
        )


def test_create_pr_request_rejects_matching_head_and_base_branches() -> None:
    with pytest.raises(ValidationError):
        github_routes.CreatePRRequest(
            session_id=str(uuid.uuid4()),
            title="Fix parser bug",
            base_branch="refs/heads/main",
            head_branch=" main ",
        )
