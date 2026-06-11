from __future__ import annotations

import codey.saas.emails.templates as templates


def test_button_escapes_href_and_label_html() -> None:
    html = templates._button(
        'https://app.example.com/?next="><script>alert(1)</script>',
        "Open <Dashboard>",
    )

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Open &lt;Dashboard&gt;" in html
    assert "&quot;&gt;" in html


def test_welcome_escapes_user_name() -> None:
    _subject, html = templates.welcome(
        name='<img src=x onerror="alert(1)">',
        dashboard_url="https://app.example.com/dashboard",
    )

    assert "<img" not in html
    assert '&lt;img src=x onerror="alert(1)"&gt;' in html


def test_autonomous_summary_escapes_action_text() -> None:
    _subject, html = templates.autonomous_summary(
        actions=[
            {
                "repo": "owner/<repo>",
                "description": "<script>alert(1)</script>",
            }
        ],
        credits_used=1,
        dashboard_url="https://app.example.com/dashboard",
    )

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "owner/&lt;repo&gt;" in html


def test_session_complete_escapes_summary_description() -> None:
    _subject, html = templates.session_complete(
        session_summary={"description": "<b>done</b>"},
        dashboard_url="https://app.example.com/session",
    )

    assert "<b>done</b>" not in html
    assert "&lt;b&gt;done&lt;/b&gt;" in html
