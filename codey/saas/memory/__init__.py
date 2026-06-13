from __future__ import annotations

__all__ = ["MemoryEngine"]


def __getattr__(name: str):
    """Lazily load memory components so package import stays dependency-light."""
    if name != "MemoryEngine":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from codey.saas.memory.engine import MemoryEngine

    return MemoryEngine
