__all__ = ["AgentOrchestrator"]


def __getattr__(name: str):
    """Lazily load agent components so package import stays dependency-light."""
    if name != "AgentOrchestrator":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from codey.saas.agents.orchestrator import AgentOrchestrator

    return AgentOrchestrator
