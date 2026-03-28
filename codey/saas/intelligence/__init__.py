from __future__ import annotations
from codey.saas.intelligence.ensemble import ModelEnsemble
from codey.saas.intelligence.research import ResearchEngine
from codey.saas.intelligence.router import TaskRouter
from codey.saas.intelligence.services import IntelligenceServices, intelligence_services

__all__ = [
    "TaskRouter",
    "ModelEnsemble",
    "IntelligenceStack",
    "ResearchEngine",
    "IntelligenceServices",
    "intelligence_services",
]


class IntelligenceStack:
    """Unified facade over routing, execution, and research."""

    def __init__(self) -> None:
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
