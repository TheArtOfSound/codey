from __future__ import annotations

__all__ = ["VaultService"]


def __getattr__(name: str):
    """Lazily load vault components so package import stays dependency-light."""
    if name != "VaultService":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from codey.saas.vault.service import VaultService

    return VaultService
