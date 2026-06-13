from __future__ import annotations

__all__ = [
    "CreditService",
    "InsufficientCreditsError",
    "CREDIT_COSTS",
    "PLAN_CREDITS",
]

_EXPORTS = {
    "CreditService": ("codey.saas.credits.service", "CreditService"),
    "InsufficientCreditsError": (
        "codey.saas.credits.service",
        "InsufficientCreditsError",
    ),
    "CREDIT_COSTS": ("codey.saas.credits.service", "CREDIT_COSTS"),
    "PLAN_CREDITS": ("codey.saas.credits.service", "PLAN_CREDITS"),
}


def __getattr__(name: str):
    """Lazily load credit components so package import stays dependency-light."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    return getattr(import_module(module_name), attribute)
