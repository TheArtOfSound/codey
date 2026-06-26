"""Codey Structural Health Dashboard — real-time NFET monitoring UI."""

__all__ = ["create_app", "DashboardState"]

_EXPORTS = {
    "DashboardState": "DashboardState",
    "create_app": "create_app",
}


def __getattr__(name: str):
    """Lazily load dashboard components so package import stays dependency-light."""
    try:
        attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    return getattr(import_module("codey.dashboard.server"), attribute)
