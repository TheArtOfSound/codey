from __future__ import annotations

__all__ = ["EmailService"]


def __getattr__(name: str):
    """Lazily load email components so package import stays dependency-light."""
    if name != "EmailService":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from codey.saas.emails.service import EmailService

    return EmailService
