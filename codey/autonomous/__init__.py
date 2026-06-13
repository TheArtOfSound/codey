"""Autonomous Mode — network-aware monitoring and self-healing for Codey."""

__all__ = ["AutonomousMonitor", "AuditDatabase", "TriggerCondition"]

_EXPORTS = {
    "AuditDatabase": ("codey.autonomous.audit_db", "AuditDatabase"),
    "AutonomousMonitor": ("codey.autonomous.monitor", "AutonomousMonitor"),
    "TriggerCondition": ("codey.autonomous.monitor", "TriggerCondition"),
}


def __getattr__(name: str):
    """Lazily load autonomous components so package import stays dependency-light."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    return getattr(import_module(module_name), attribute)
