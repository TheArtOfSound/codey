"""Persistent audit trail for autonomous actions — every change Codey makes on its own gets logged."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS autonomous_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    trigger_condition TEXT,
    component_affected TEXT,
    stress_before REAL,
    stress_after REAL,
    kappa_before REAL,
    kappa_after REAL,
    sigma_before REAL,
    sigma_after REAL,
    es_before REAL,
    es_after REAL,
    change_diff TEXT,
    test_result TEXT,
    rolled_back INTEGER DEFAULT 0
);
"""


class AuditDatabase:
    """SQLite-backed audit log for every autonomous action Codey performs.

    Every modification — successful or rolled back — is recorded with full
    before/after NFET metrics so that any action can be reviewed, correlated
    with health trends, and used for future decision calibration.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.  Defaults to ``codey_audit.db``
        in the current working directory.
    """

    def __init__(self, db_path: str = "codey_audit.db") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log_action(
        self,
        trigger_condition: str,
        component_affected: str,
        stress_before: float,
        stress_after: float,
        kappa_before: float,
        kappa_after: float,
        sigma_before: float,
        sigma_after: float,
        es_before: float,
        es_after: float,
        change_diff: str,
        test_result: str,
        rolled_back: bool = False,
    ) -> int:
        """Insert a fully-detailed audit record.

        Returns the row ID of the inserted record.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            """\
            INSERT INTO autonomous_actions
                (timestamp, trigger_condition, component_affected,
                 stress_before, stress_after,
                 kappa_before, kappa_after,
                 sigma_before, sigma_after,
                 es_before, es_after,
                 change_diff, test_result, rolled_back)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                trigger_condition,
                component_affected,
                stress_before,
                stress_after,
                kappa_before,
                kappa_after,
                sigma_before,
                sigma_after,
                es_before,
                es_after,
                change_diff,
                test_result,
                1 if rolled_back else 0,
            ),
        )
        self._conn.commit()
        row_id = cursor.lastrowid
        logger.info(
            "Audit record #%d: trigger=%s component=%s rolled_back=%s",
            row_id,
            trigger_condition,
            component_affected,
            rolled_back,
        )
        return row_id  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_recent(self, limit: int = 20) -> list[dict]:
        """Return the most recent autonomous actions, newest first."""
        cursor = self._conn.execute(
            "SELECT * FROM autonomous_actions ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_actions_for_component(self, component: str) -> list[dict]:
        """Return all autonomous actions that affected a specific component."""
        cursor = self._conn.execute(
            "SELECT * FROM autonomous_actions WHERE component_affected = ? ORDER BY id DESC",
            (component,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_rollback_count(self) -> int:
        """Count how many autonomous actions were rolled back.

        A high rollback rate signals that the autonomous config is too
        aggressive or that the scoring heuristic needs recalibration.
        """
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM autonomous_actions WHERE rolled_back = 1"
        )
        row = cursor.fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> AuditDatabase:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
