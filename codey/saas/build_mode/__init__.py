"""Build Mode — autonomous project generation engine for Codey SaaS."""

__all__ = [
    "BuildContext",
    "BuildEngine",
    "FileGenerator",
    "FileSummary",
    "GeneratedFile",
    "ProjectPlanner",
    "TaskDecomposer",
    "TaskNode",
    "TemplateLibrary",
]

_EXPORTS = {
    "BuildContext": ("codey.saas.build_mode.generator", "BuildContext"),
    "BuildEngine": ("codey.saas.build_mode.engine", "BuildEngine"),
    "FileGenerator": ("codey.saas.build_mode.generator", "FileGenerator"),
    "FileSummary": ("codey.saas.build_mode.generator", "FileSummary"),
    "GeneratedFile": ("codey.saas.build_mode.generator", "GeneratedFile"),
    "ProjectPlanner": ("codey.saas.build_mode.planner", "ProjectPlanner"),
    "TaskDecomposer": ("codey.saas.build_mode.decomposer", "TaskDecomposer"),
    "TaskNode": ("codey.saas.build_mode.decomposer", "TaskNode"),
    "TemplateLibrary": ("codey.saas.build_mode.templates", "TemplateLibrary"),
}


def __getattr__(name: str):
    """Lazily load build-mode components so lightweight imports stay usable."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    return getattr(import_module(module_name), attribute)
