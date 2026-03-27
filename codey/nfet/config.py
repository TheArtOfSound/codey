"""NFET configuration loader.

Reads ``codey.config.json`` from the project root and provides validated
calibration values for the NFET sweep engine.  Falls back to sensible
defaults if the file is absent or fields are missing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "codey.config.json"


@dataclass
class NFETConfig:
    """All calibration values for the NFET sweep engine."""

    # Core sweep parameters
    alpha: float = 1.0
    beta: float = 2.0
    sigma_star: float = 0.5
    kappa_star: float = 0.4
    kappa_max: float = 1.0

    # Phase thresholds
    ridge_threshold: float = 0.7
    caution_threshold: float = 0.4

    # Stress normalization
    stress_scale: float = 10.0

    # Change impact sensitivity
    impact_es_significant: float = 0.05
    impact_phase_change_alert: bool = True

    # Autonomous mode triggers
    auto_sweep_on_commit: bool = True
    auto_sweep_interval_minutes: int = 30
    critical_phase_block_deploy: bool = False

    # Dashboard display
    history_retention_days: int = 90
    top_stress_components_shown: int = 5

    # Credit cost for NFET sweep
    sweep_credit_cost: int = 0


def load_config(project_root: str | Path | None = None) -> NFETConfig:
    """Load NFET configuration from ``codey.config.json``.

    Parameters
    ----------
    project_root
        Directory containing the config file.  If *None*, searches the
        current working directory and up to 5 parent directories.

    Returns
    -------
    NFETConfig
        Validated configuration with defaults for any missing fields.
    """
    config_path = _find_config(project_root)
    if config_path is None:
        logger.debug("No %s found, using defaults", _CONFIG_FILENAME)
        return NFETConfig()

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to parse %s: %s — using defaults", config_path, exc)
        return NFETConfig()

    nfet_section = raw.get("nfet", raw)
    if not isinstance(nfet_section, dict):
        logger.warning("'nfet' key in config is not a dict — using defaults")
        return NFETConfig()

    config = NFETConfig()
    _apply_overrides(config, nfet_section)

    errors = validate_config(config)
    if errors:
        for err in errors:
            logger.warning("Config validation: %s", err)
        # Still return the config — warnings are non-fatal; out-of-range
        # values are clamped rather than rejected.
        _clamp_config(config)

    logger.info("Loaded NFET config from %s", config_path)
    return config


def validate_config(config: NFETConfig) -> list[str]:
    """Validate a config and return a list of error strings (empty if valid)."""
    errors: list[str] = []

    if config.alpha <= 0:
        errors.append(f"alpha must be positive, got {config.alpha}")
    if config.beta <= 0:
        errors.append(f"beta must be positive, got {config.beta}")
    if not 0 <= config.sigma_star <= 1:
        errors.append(f"sigma_star must be in [0, 1], got {config.sigma_star}")
    if not 0 <= config.kappa_star <= 1:
        errors.append(f"kappa_star must be in [0, 1], got {config.kappa_star}")
    if config.kappa_max <= 0:
        errors.append(f"kappa_max must be positive, got {config.kappa_max}")
    if not 0 < config.ridge_threshold <= 1:
        errors.append(f"ridge_threshold must be in (0, 1], got {config.ridge_threshold}")
    if not 0 < config.caution_threshold < config.ridge_threshold:
        errors.append(
            f"caution_threshold must be in (0, ridge_threshold), "
            f"got {config.caution_threshold}"
        )
    if config.stress_scale <= 0:
        errors.append(f"stress_scale must be positive, got {config.stress_scale}")
    if config.auto_sweep_interval_minutes < 1:
        errors.append(
            f"auto_sweep_interval_minutes must be >= 1, "
            f"got {config.auto_sweep_interval_minutes}"
        )
    if config.history_retention_days < 1:
        errors.append(
            f"history_retention_days must be >= 1, "
            f"got {config.history_retention_days}"
        )
    if config.sweep_credit_cost < 0:
        errors.append(f"sweep_credit_cost must be >= 0, got {config.sweep_credit_cost}")

    return errors


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _find_config(project_root: str | Path | None) -> Path | None:
    """Locate the config file by searching upward from the project root."""
    if project_root is not None:
        candidate = Path(project_root) / _CONFIG_FILENAME
        return candidate if candidate.is_file() else None

    cwd = Path.cwd()
    for _ in range(6):  # cwd + 5 parents
        candidate = cwd / _CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        parent = cwd.parent
        if parent == cwd:
            break
        cwd = parent
    return None


def _apply_overrides(config: NFETConfig, overrides: dict[str, Any]) -> None:
    """Apply JSON overrides to a config dataclass, ignoring unknown keys."""
    field_names = {f.name for f in config.__dataclass_fields__.values()}
    for key, value in overrides.items():
        if key in field_names:
            expected_type = type(getattr(config, key))
            try:
                coerced = expected_type(value)
                setattr(config, key, coerced)
            except (TypeError, ValueError):
                logger.warning(
                    "Cannot coerce config key '%s' value %r to %s — skipping",
                    key, value, expected_type.__name__,
                )


def _clamp_config(config: NFETConfig) -> None:
    """Clamp out-of-range values to valid bounds."""
    config.alpha = max(0.01, config.alpha)
    config.beta = max(0.01, config.beta)
    config.sigma_star = max(0.0, min(1.0, config.sigma_star))
    config.kappa_star = max(0.0, min(1.0, config.kappa_star))
    config.kappa_max = max(0.01, config.kappa_max)
    config.ridge_threshold = max(0.01, min(1.0, config.ridge_threshold))
    config.caution_threshold = max(0.01, min(config.ridge_threshold - 0.01, config.caution_threshold))
    config.stress_scale = max(0.01, config.stress_scale)
    config.auto_sweep_interval_minutes = max(1, config.auto_sweep_interval_minutes)
    config.history_retention_days = max(1, config.history_retention_days)
    config.sweep_credit_cost = max(0, config.sweep_credit_cost)
