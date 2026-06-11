
__all__ = [
    "ApiKey",
    "Base",
    "BuildCheckpoint",
    "BuildFile",
    "BuildProject",
    "CodingSession",
    "CreditTransaction",
    "Export",
    "MemoryUpdateLog",
    "Project",
    "ProjectVersion",
    "Referral",
    "Repository",
    "SecurityAuditLog",
    "SessionCost",
    "User",
    "UserMemory",
]

_EXPORTS = {
    "ApiKey": ("codey.saas.models.api_key", "ApiKey"),
    "Base": ("codey.saas.models.base", "Base"),
    "BuildCheckpoint": ("codey.saas.models.build_checkpoint", "BuildCheckpoint"),
    "BuildFile": ("codey.saas.models.build_file", "BuildFile"),
    "BuildProject": ("codey.saas.models.build_project", "BuildProject"),
    "CodingSession": ("codey.saas.models.coding_session", "CodingSession"),
    "CreditTransaction": (
        "codey.saas.models.credit_transaction",
        "CreditTransaction",
    ),
    "Export": ("codey.saas.models.export", "Export"),
    "MemoryUpdateLog": ("codey.saas.models.memory_update_log", "MemoryUpdateLog"),
    "Project": ("codey.saas.models.project", "Project"),
    "ProjectVersion": ("codey.saas.models.project_version", "ProjectVersion"),
    "Referral": ("codey.saas.models.referral", "Referral"),
    "Repository": ("codey.saas.models.repository", "Repository"),
    "SecurityAuditLog": ("codey.saas.models.security_audit_log", "SecurityAuditLog"),
    "SessionCost": ("codey.saas.models.cost_tracking", "SessionCost"),
    "User": ("codey.saas.models.user", "User"),
    "UserMemory": ("codey.saas.models.user_memory", "UserMemory"),
}


def __getattr__(name: str):
    """Lazily load model classes so package import stays dependency-light."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    return getattr(import_module(module_name), attribute)
