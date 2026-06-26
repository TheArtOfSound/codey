from __future__ import annotations

__all__ = [
    "AuditLogger",
    "RateLimiter",
    "SecurityMiddleware",
    "decrypt_token",
    "encrypt_token",
    "verify_ownership",
]

_EXPORTS = {
    "AuditLogger": ("codey.saas.security.audit", "AuditLogger"),
    "RateLimiter": ("codey.saas.security.rate_limiter", "RateLimiter"),
    "SecurityMiddleware": ("codey.saas.security.middleware", "SecurityMiddleware"),
    "decrypt_token": ("codey.saas.security.encryption", "decrypt_token"),
    "encrypt_token": ("codey.saas.security.encryption", "encrypt_token"),
    "verify_ownership": ("codey.saas.security.ownership", "verify_ownership"),
}


def __getattr__(name: str):
    """Lazily load security components so package import stays dependency-light."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    return getattr(import_module(module_name), attribute)
