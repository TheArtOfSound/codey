from __future__ import annotations


def _wrap(body_html: str) -> str:
    """Wrap body content in the standard Codey email shell."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Codey</title>
</head>
<body style="margin:0;padding:0;background-color:#1a1a2e;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#1a1a2e;padding:40px 20px;">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.3);">
<!-- Header -->
<tr><td style="background-color:#1a1a2e;padding:28px 40px;text-align:center;">
<span style="font-size:28px;font-weight:700;color:#00ff88;letter-spacing:-0.5px;">Codey</span>
</td></tr>
<!-- Body -->
<tr><td style="padding:36px 40px 40px;">
{body_html}
</td></tr>
<!-- Footer -->
<tr><td style="padding:20px 40px 28px;border-top:1px solid #eee;text-align:center;">
<p style="margin:0;font-size:12px;color:#999;line-height:1.5;">
You're receiving this because you have a Codey account.<br/>
&copy; 2026 Codey &mdash; AI-powered development
</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _heading(text: str) -> str:
    return f'<h1 style="margin:0 0 16px;font-size:22px;font-weight:700;color:#1a1a2e;">{text}</h1>'


def _paragraph(text: str) -> str:
    return f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#444;">{text}</p>'


def _button(url: str, label: str) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0;">'
        f'<tr><td style="border-radius:8px;background-color:#00ff88;" align="center">'
        f'<a href="{url}" target="_blank" '
        f'style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:600;'
        f'color:#1a1a2e;text-decoration:none;border-radius:8px;">{label}</a>'
        f'</td></tr></table>'
    )


def _stat_row(label: str, value: str) -> str:
    return (
        f'<tr>'
        f'<td style="padding:8px 0;font-size:14px;color:#888;border-bottom:1px solid #f0f0f0;">{label}</td>'
        f'<td style="padding:8px 0;font-size:14px;font-weight:600;color:#1a1a2e;text-align:right;border-bottom:1px solid #f0f0f0;">{value}</td>'
        f'</tr>'
    )


def _stats_table(rows: list[tuple[str, str]]) -> str:
    inner = "".join(_stat_row(l, v) for l, v in rows)
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:16px 0 24px;">{inner}</table>'
    )


# ---------------------------------------------------------------------------
# Template functions — each returns (subject, html_body)
# ---------------------------------------------------------------------------


def welcome(*, name: str, dashboard_url: str, credits: int = 10) -> tuple[str, str]:
    subject = "Welcome to Codey. You have 10 free credits."
    body = (
        _heading(f"Welcome, {name}!")
        + _paragraph(
            "You're all set. Codey is an AI-powered coding assistant that writes, "
            "reviews, and ships code for you &mdash; autonomously or on demand."
        )
        + _paragraph("<strong>Quick start:</strong>")
        + _paragraph(
            "1. Connect your first repository from the dashboard.<br/>"
            "2. Describe what you want built in plain English.<br/>"
            "3. Codey writes the code, opens a PR, and explains every change."
        )
        + _stats_table([("Starting credits", str(credits))])
        + _button(dashboard_url, "Open Dashboard")
    )
    return subject, _wrap(body)


def email_verification(*, verification_url: str) -> tuple[str, str]:
    subject = "Verify your Codey email."
    body = (
        _heading("Verify your email")
        + _paragraph(
            "Click the button below to verify your email address. "
            "This link is valid for <strong>24 hours</strong>."
        )
        + _button(verification_url, "Verify Email")
        + _paragraph(
            '<span style="font-size:13px;color:#999;">'
            "If you didn't create a Codey account, you can safely ignore this email."
            "</span>"
        )
    )
    return subject, _wrap(body)


def payment_success(
    *, amount_cents: int, credits_added: int, new_balance: int
) -> tuple[str, str]:
    subject = "Payment confirmed \u2014 credits added."
    dollars = f"${amount_cents / 100:,.2f}"
    body = (
        _heading("Payment received")
        + _paragraph("Your payment has been processed and credits have been added to your account.")
        + _stats_table(
            [
                ("Amount paid", dollars),
                ("Credits added", f"+{credits_added:,}"),
                ("New balance", f"{new_balance:,} credits"),
            ]
        )
        + _paragraph("Happy building!")
    )
    return subject, _wrap(body)


def payment_failed(*, dashboard_url: str) -> tuple[str, str]:
    subject = "Action required: payment failed."
    body = (
        _heading("Payment failed")
        + _paragraph(
            "We weren't able to process your most recent payment. "
            "This is usually caused by an expired card or insufficient funds."
        )
        + _paragraph(
            '<span style="color:#e74c3c;font-weight:600;">'
            "You have 3 days to update your payment method before your account is paused."
            "</span>"
        )
        + _button(dashboard_url + "/settings/billing", "Update Payment Method")
    )
    return subject, _wrap(body)


def low_credits(
    *, remaining: int, monthly: int, topup_url: str
) -> tuple[str, str]:
    subject = "Running low on Codey credits."
    pct = round((1 - remaining / monthly) * 100) if monthly > 0 else 0
    body = (
        _heading("Credits running low")
        + _paragraph(
            f"You've used <strong>{pct}%</strong> of your credits this cycle."
        )
        + _stats_table(
            [
                ("Remaining", f"{remaining:,} credits"),
                ("Used this period", f"{pct}%"),
            ]
        )
        + _paragraph("Top up now so Codey can keep working for you.")
        + _button(topup_url, "Buy More Credits")
    )
    return subject, _wrap(body)


def credits_exhausted(*, topup_url: str) -> tuple[str, str]:
    subject = "You're out of credits."
    body = (
        _heading("Credits exhausted")
        + _paragraph(
            "Your Codey credit balance has reached zero. "
            "Any running sessions have been paused."
        )
        + _paragraph("Add credits or upgrade your plan to resume.")
        + _button(topup_url, "Get More Credits")
    )
    return subject, _wrap(body)


def autonomous_summary(
    *, actions: list[dict], credits_used: int, dashboard_url: str
) -> tuple[str, str]:
    count = len(actions)
    subject = f"Codey made {count} improvement{'s' if count != 1 else ''} last night."

    rows = ""
    for action in actions:
        desc = action.get("description", "Improvement")
        repo = action.get("repo", "")
        badge = (
            f'<span style="display:inline-block;background:#f0f0f0;border-radius:4px;'
            f'padding:2px 8px;font-size:12px;color:#666;margin-right:6px;">{repo}</span>'
            if repo
            else ""
        )
        rows += (
            f'<tr><td style="padding:10px 0;border-bottom:1px solid #f5f5f5;font-size:14px;color:#444;">'
            f"{badge}{desc}</td></tr>"
        )

    actions_table = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:16px 0;">{rows}</table>'
    )

    body = (
        _heading(f"{count} autonomous improvement{'s' if count != 1 else ''}")
        + _paragraph("While you were away, Codey worked on the following:")
        + actions_table
        + _stats_table([("Credits used", f"{credits_used:,}")])
        + _button(dashboard_url, "Review Changes")
    )
    return subject, _wrap(body)


def session_complete(*, session_summary: dict, dashboard_url: str) -> tuple[str, str]:
    subject = "Your Codey session is complete."
    description = session_summary.get("description", "Session completed")
    lines = session_summary.get("lines_generated", 0)
    files_changed = session_summary.get("files_changed", 0)
    credits_charged = session_summary.get("credits_charged", 0)
    duration_min = session_summary.get("duration_minutes", 0)

    body = (
        _heading("Session complete")
        + _paragraph(description)
        + _stats_table(
            [
                ("Lines generated", f"{lines:,}"),
                ("Files changed", str(files_changed)),
                ("Duration", f"{duration_min} min"),
                ("Credits charged", str(credits_charged)),
            ]
        )
        + _button(dashboard_url, "View Full Report")
    )
    return subject, _wrap(body)


def subscription_cancelled(
    *, end_date: str, resubscribe_url: str
) -> tuple[str, str]:
    subject = "Subscription cancelled."
    body = (
        _heading("Subscription cancelled")
        + _paragraph(
            f"Your Codey subscription has been cancelled. "
            f"You'll continue to have full access until <strong>{end_date}</strong>."
        )
        + _paragraph("If you change your mind, you can resubscribe at any time.")
        + _button(resubscribe_url, "Resubscribe")
    )
    return subject, _wrap(body)


def password_reset(*, reset_url: str) -> tuple[str, str]:
    subject = "Reset your Codey password."
    body = (
        _heading("Password reset")
        + _paragraph(
            "Someone requested a password reset for your Codey account. "
            "Click the button below to choose a new password. "
            "This link is valid for <strong>1 hour</strong>."
        )
        + _button(reset_url, "Reset Password")
        + _paragraph(
            '<span style="font-size:13px;color:#999;">'
            "If you didn't request this, you can safely ignore this email. "
            "Your password will not be changed."
            "</span>"
        )
    )
    return subject, _wrap(body)
