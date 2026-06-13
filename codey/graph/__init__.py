"""Codebase Graph Engine — real-time NetworkX directed graph of code structure."""

__all__ = ["CodebaseGraph"]


def __getattr__(name: str):
    """Lazily load graph components so package import stays dependency-light."""
    if name != "CodebaseGraph":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from codey.graph.engine import CodebaseGraph

    return CodebaseGraph
