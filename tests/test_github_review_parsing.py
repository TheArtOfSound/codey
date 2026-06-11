from __future__ import annotations

import codey.saas.api.github_routes as github_routes


class _OverflowingScore:
    def __float__(self) -> float:
        raise OverflowError("score too large")


class _OverflowingLine:
    def __int__(self) -> int:
        raise OverflowError("line too large")


def test_parse_review_response_falls_back_for_non_dict_json() -> None:
    review = github_routes._parse_review_response('["not", "an", "object"]')

    assert review.summary == '["not", "an", "object"]'
    assert review.score == 0.5
    assert review.comments == []
    assert review.approved is False


def test_parse_review_response_tolerates_malformed_score_and_comments() -> None:
    review = github_routes._parse_review_response(
        """
        {
          "summary": "Needs work",
          "score": "high",
          "comments": [
            {"path": "app.py", "line": "12", "body": "Fix this", "severity": "warning"},
            "not-a-comment",
            {"path": "other.py", "line": "oops", "body": "Check this"}
          ],
          "approved": false
        }
        """
    )

    assert review.summary == "Needs work"
    assert review.score == 0.5
    assert len(review.comments) == 2
    assert review.comments[0].line == 12
    assert review.comments[1].line is None
    assert review.approved is False


def test_parse_review_response_parses_string_booleans_and_clamps_score() -> None:
    review = github_routes._parse_review_response(
        """
        {
          "summary": "Looks good",
          "score": 1.7,
          "comments": [],
          "approved": "false"
        }
        """
    )

    assert review.score == 1.0
    assert review.approved is False


def test_parse_review_response_falls_back_for_non_finite_score() -> None:
    for score in ("nan", "inf", "-inf"):
        review = github_routes._parse_review_response(
            f"""
            {{
              "summary": "Non-finite score",
              "score": "{score}",
              "comments": [],
              "approved": false
            }}
            """
        )

        assert review.score == 0.5


def test_parse_review_response_falls_back_for_score_conversion_overflow(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        github_routes.json,
        "loads",
        lambda _raw: {
            "summary": "Overflowing score",
            "score": _OverflowingScore(),
            "comments": [],
            "approved": False,
        },
    )

    review = github_routes._parse_review_response("{}")

    assert review.score == 0.5


def test_parse_review_response_drops_comment_line_conversion_overflow(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        github_routes.json,
        "loads",
        lambda _raw: {
            "summary": "Overflowing line",
            "score": 0.8,
            "comments": [
                {
                    "path": "app.py",
                    "line": _OverflowingLine(),
                    "body": "Check this",
                    "severity": "warning",
                }
            ],
            "approved": False,
        },
    )

    review = github_routes._parse_review_response("{}")

    assert len(review.comments) == 1
    assert review.comments[0].line is None
    assert review.comments[0].severity == "warning"


def test_parse_review_response_fails_closed_for_unknown_approved_string() -> None:
    review = github_routes._parse_review_response(
        """
        {
          "summary": "Unclear approval state",
          "score": 0.8,
          "comments": [],
          "approved": "pending"
        }
        """
    )

    assert review.approved is False


def test_parse_review_response_tolerates_non_string_summary_and_comment_fields() -> None:
    review = github_routes._parse_review_response(
        """
        {
          "summary": ["not", "a", "string"],
          "score": 0.4,
          "comments": [
            {"path": ["app.py"], "line": "7", "body": {"text": "Fix this"}, "severity": 5}
          ],
          "approved": true
        }
        """
    )

    assert review.summary == ""
    assert len(review.comments) == 1
    assert review.comments[0].path == ""
    assert review.comments[0].line == 7
    assert review.comments[0].body == ""
    assert review.comments[0].severity == "suggestion"
    assert review.approved is True


def test_parse_review_response_sanitizes_comment_line_and_severity() -> None:
    review = github_routes._parse_review_response(
        """
        {
          "summary": "Needs work",
          "score": 0.4,
          "comments": [
            {"path": "app.py", "line": 0, "body": "Bad line", "severity": "critical"},
            {"path": "ok.py", "line": 1, "body": "Valid", "severity": "WARNING"}
          ],
          "approved": false
        }
        """
    )

    assert review.comments[0].line is None
    assert review.comments[0].severity == "suggestion"
    assert review.comments[1].line == 1
    assert review.comments[1].severity == "warning"


def test_parse_review_response_rejects_boolean_comment_lines() -> None:
    review = github_routes._parse_review_response(
        """
        {
          "summary": "Needs work",
          "score": 0.4,
          "comments": [
            {"path": "app.py", "line": true, "body": "Bad line", "severity": "warning"}
          ],
          "approved": false
        }
        """
    )

    assert review.comments[0].line is None
