"""Persistent SQLite store for NFET sweep snapshots and trend analysis."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from scipy.stats import linregress

if TYPE_CHECKING:
    from codey.nfet.sweep import SweepResult

logger = logging.getLogger(__name__)

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
        self._conn.execute(
            """\
            INSERT INTO sweep_snapshots
                (timestamp, kappa, sigma, es_score, phase,
                 highest_stress_component, highest_stress_value,
                 total_nodes, total_edges, mean_coupling, mean_cohesion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.timestamp,
                result.kappa,
                result.sigma,
                result.es_score,
                result.phase.value,
                result.highest_stress_component,
                result.highest_stress_value,
                result.total_nodes,
                result.total_edges,
                result.mean_coupling,
                result.mean_cohesion,
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_history(self, hours: int = 24) -> list[dict]:
        """Return snapshots from the last *hours* hours, oldest first."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        cursor = self._conn.execute(
            "SELECT * FROM sweep_snapshots WHERE timestamp >= ? ORDER BY timestamp ASC",
            (cutoff,),
        )
        return [dict(row) for row in cursor.fetchall()]

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

        es_slope = linregress(indices, es_values).slope
        kappa_slope = linregress(indices, kappa_values).slope
        sigma_slope = linregress(indices, sigma_values).slope

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
