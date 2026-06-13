from __future__ import annotations

__all__ = [
    "TaskRouter",
    "ModelEnsemble",
    "IntelligenceStack",
    "ResearchEngine",
    "IntelligenceServices",
    "intelligence_services",
]


def __getattr__(name: str):
    """Lazily load heavy intelligence components on demand."""
    if name == "TaskRouter":
        from codey.saas.intelligence.router import TaskRouter

        return TaskRouter
    if name == "ModelEnsemble":
        from codey.saas.intelligence.ensemble import ModelEnsemble

        return ModelEnsemble
    if name == "ResearchEngine":
        from codey.saas.intelligence.research import ResearchEngine

        return ResearchEngine
    if name in {"IntelligenceServices", "intelligence_services"}:
        from codey.saas.intelligence.services import (
            IntelligenceServices,
            intelligence_services,
        )

        return {
            "IntelligenceServices": IntelligenceServices,
            "intelligence_services": intelligence_services,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class IntelligenceStack:
    """Unified facade over routing, execution, and research."""

    def __init__(self) -> None:
        from codey.saas.intelligence.ensemble import ModelEnsemble
        from codey.saas.intelligence.research import ResearchEngine
        from codey.saas.intelligence.router import TaskRouter

        self.router = TaskRouter()
        self.ensemble = ModelEnsemble()
        self.research = ResearchEngine()

    async def run(
        self,
        request: str,
        messages: list[dict[str, str]],
        context: dict | None = None,
    ):
        """Route, execute, and return the result."""
        context = context or {}
        config = self.router.classify(request, context)
        return await self.ensemble.execute(config, messages, context)
