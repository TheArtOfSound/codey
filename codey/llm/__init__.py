"""LLM integration layer — structurally-aware code generation via Claude."""

__all__ = ["PromptBuilder", "CodeAgent"]

_EXPORTS = {
    "CodeAgent": ("codey.llm.code_agent", "CodeAgent"),
    "PromptBuilder": ("codey.llm.prompt_builder", "PromptBuilder"),
}


def __getattr__(name: str):
    """Lazily load LLM components so package import stays dependency-light."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    return getattr(import_module(module_name), attribute)
