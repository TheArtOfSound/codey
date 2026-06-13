from __future__ import annotations

__all__ = [
    "AuthService",
    "create_access_token",
    "get_current_user",
    "oauth_github_url",
    "oauth_google_url",
]

_EXPORTS = {
    "AuthService": ("codey.saas.auth.service", "AuthService"),
    "create_access_token": ("codey.saas.auth.jwt", "create_access_token"),
    "get_current_user": ("codey.saas.auth.dependencies", "get_current_user"),
    "oauth_github_url": ("codey.saas.auth.oauth", "oauth_github_url"),
    "oauth_google_url": ("codey.saas.auth.oauth", "oauth_google_url"),
}


def __getattr__(name: str):
    """Lazily load auth components so package import stays dependency-light."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    return getattr(import_module(module_name), attribute)
