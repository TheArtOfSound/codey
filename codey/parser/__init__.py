"""Parser layer — extracts AST nodes and edges from source files using tree-sitter."""

__all__ = ["CodeNode", "CodeEdge", "LanguageParser", "parse_directory"]

_EXPORTS = {
    "CodeEdge": "CodeEdge",
    "CodeNode": "CodeNode",
    "LanguageParser": "LanguageParser",
    "parse_directory": "parse_directory",
}


def __getattr__(name: str):
    """Lazily load parser components so package import stays dependency-light."""
    try:
        attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    return getattr(import_module("codey.parser.extractor"), attribute)
