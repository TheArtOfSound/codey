"""Persistent SQLite store for NFET sweep snapshots and trend analysis."""

from __future__ import annotations

import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codey.nfet.sweep import SweepResult

logger = logging.getLogger(__name__)

_MAX_HISTORY_COUNT = 1_000_000_000
_DEFAULT_HISTORY_HOURS = 24
_MAX_HISTORY_HOURS = 24 * 365 * 10

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS sweep_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    kappa REAL,
    sigma REAL,
    es_score REAL,
    phase TEXT,
    highest_stress_component TEXT,
    highest_stress_value REAL,
    total_nodes INTEGER,
    total_edges INTEGER,
    mean_coupling REAL,
    mean_cohesion REAL
);
"""


def _coerce_history_row_list(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _history_row_to_dict(row: object) -> dict | None:
    try:
        return dict(row)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _coerce_history_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return normalized if math.isfinite(normalized) else default


def _coerce_history_int(
    value: object,
    default: int = 0,
    maximum: int = _MAX_HISTORY_COUNT,
) -> int:
    if isinstance(value, bool):
        return default
    try:
        normalized = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    if normalized < 0:
        return default
    return min(normalized, maximum)


def _coerce_history_hours(
    value: object,
    default: int = _DEFAULT_HISTORY_HOURS,
    maximum: int = _MAX_HISTORY_HOURS,
) -> int:
    if isinstance(value, bool):
        return default
    try:
        normalized = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    if normalized <= 0:
        return default
    return min(normalized, maximum)


def _coerce_history_text(value: object, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip()
    else:
        normalized = str(value).strip()
    return normalized or default


def _linear_regression_slope(x_values: list[int], y_values: list[object]) -> float:
    if len(x_values) != len(y_values) or len(x_values) < 2:
        return 0.0

    try:
        xs = [float(value) for value in x_values]
        ys = [float(value) for value in y_values]
    except (OverflowError, TypeError, ValueError):
        return 0.0
    if not all(math.isfinite(value) for value in xs + ys):
        return 0.0

    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    return numerator / denominator


class HealthDatabase:
    """SQLite-backed storage for NFET sweep history.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.  Defaults to ``codey_health.db``
        in the current working directory.
    """

    def __init__(self, db_path: str = "codey_health.db") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log_sweep(self, result: SweepResult) -> None:
        """Persist a sweep result snapshot."""
        raw_phase = getattr(result, "phase", None)
        phase = _coerce_history_text(getattr(raw_phase, "value", raw_phase))
        self._conn.execute(
            """\
            INSERT INTO sweep_snapshots
                (timestamp, kappa, sigma, es_score, phase,
                 highest_stress_component, highest_stress_value,
                 total_nodes, total_edges, mean_coupling, mean_cohesion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _coerce_history_text(result.timestamp),
                _coerce_history_float(result.kappa),
                _coerce_history_float(result.sigma),
                _coerce_history_float(result.es_score),
                phase,
                _coerce_history_text(result.highest_stress_component),
                _coerce_history_float(result.highest_stress_value),
                _coerce_history_int(result.total_nodes),
                _coerce_history_int(result.total_edges),
                _coerce_history_float(result.mean_coupling),
                _coerce_history_float(result.mean_cohesion),
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_history(self, hours: int = 24) -> list[dict]:
        """Return snapshots from the last *hours* hours, oldest first."""
        safe_hours = _coerce_history_hours(hours)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=safe_hours)
        ).isoformat()
        cursor = self._conn.execute(
            "SELECT * FROM sweep_snapshots WHERE timestamp >= ? ORDER BY timestamp ASC",
            (cutoff,),
        )
        snapshots = []
        for row in _coerce_history_row_list(cursor.fetchall()):
            snapshot = _history_row_to_dict(row)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def get_latest(self) -> dict | None:
        """Return the most recent snapshot, or ``None`` if the table is empty."""
        cursor = self._conn.execute(
            "SELECT * FROM sweep_snapshots ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Trend analysis
    # ------------------------------------------------------------------

    def get_trend(self, hours: int = 24) -> dict:
        """Compute directional trends for ES, kappa, and sigma over recent history.

        Uses ordinary least-squares linear regression on the time-ordered
        snapshots.  A slope whose absolute value is below a small threshold
        (1e-6) is reported as ``"stable"``.

        Returns
        -------
        dict
            Keys: ``es_direction``, ``kappa_direction``, ``sigma_direction``.
            Each value is one of ``"improving"``, ``"declining"``, or ``"stable"``.
        """
        snapshots = self.get_history(hours)

        if len(snapshots) < 2:
            return {
                "es_direction": "stable",
                "kappa_direction": "stable",
                "sigma_direction": "stable",
            }

        indices = list(range(len(snapshots)))

        es_values = [s["es_score"] for s in snapshots]
        kappa_values = [s["kappa"] for s in snapshots]
        sigma_values = [s["sigma"] for s in snapshots]

        es_slope = _linear_regression_slope(indices, es_values)
        kappa_slope = _linear_regression_slope(indices, kappa_values)
        sigma_slope = _linear_regression_slope(indices, sigma_values)

        threshold = 1e-6

        def _direction(slope: float, higher_is_better: bool) -> str:
            if abs(slope) < threshold:
                return "stable"
            positive = slope > 0
            if higher_is_better:
                return "improving" if positive else "declining"
            return "declining" if positive else "improving"

        return {
            "es_direction": _direction(es_slope, higher_is_better=True),
            # Lower kappa means less coupling — generally better
            "kappa_direction": _direction(kappa_slope, higher_is_better=False),
            # Higher sigma means more cascade margin — better
            "sigma_direction": _direction(sigma_slope, higher_is_better=True),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> HealthDatabase:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
