__all__ = ["ProjectWiki"]


def __getattr__(name: str):
    """Lazily load wiki components so package import stays dependency-light."""
    if name != "ProjectWiki":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from codey.saas.wiki.generator import ProjectWiki

    return ProjectWiki
