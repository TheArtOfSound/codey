"""Real-time session execution and WebSocket streaming."""

from __future__ import annotations

__all__ = ["SessionRunner", "SessionStream"]

_EXPORTS = {
    "SessionRunner": ("codey.saas.sessions.runner", "SessionRunner"),
    "SessionStream": ("codey.saas.sessions.stream", "SessionStream"),
}


def __getattr__(name: str):
    """Lazily load session components so package import stays dependency-light."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    return getattr(import_module(module_name), attribute)
