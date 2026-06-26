"""NFET (Network Flow Equilibrium Topology) sweep engine for codebase structural health."""

__all__ = [
    "ActionCandidate",
    "ActionSimulation",
    "ControllerWeights",
    "HealthDatabase",
    "NFETController",
    "NFETSweep",
    "NodeState",
    "Phase",
    "RepoState",
    "SweepResult",
]

_EXPORTS = {
    "ActionCandidate": ("codey.nfet.state", "ActionCandidate"),
    "ActionSimulation": ("codey.nfet.state", "ActionSimulation"),
    "ControllerWeights": ("codey.nfet.controller", "ControllerWeights"),
    "HealthDatabase": ("codey.nfet.health_db", "HealthDatabase"),
    "NFETController": ("codey.nfet.controller", "NFETController"),
    "NFETSweep": ("codey.nfet.sweep", "NFETSweep"),
    "NodeState": ("codey.nfet.state", "NodeState"),
    "Phase": ("codey.nfet.sweep", "Phase"),
    "RepoState": ("codey.nfet.state", "RepoState"),
    "SweepResult": ("codey.nfet.sweep", "SweepResult"),
}


def __getattr__(name: str):
    """Lazily load NFET components so state imports stay dependency-light."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    return getattr(import_module(module_name), attribute)
