from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.models.security_audit_log import SecurityAuditLog

# Actions that are tracked — kept as constants so callers use canonical strings.
ACTION_LOGIN_SUCCESS = "login_success"
ACTION_LOGIN_FAILURE = "login_failure"
ACTION_LOGOUT = "logout"
ACTION_PASSWORD_CHANGE = "password_change"
ACTION_MFA_ENABLE = "mfa_enable"
ACTION_MFA_DISABLE = "mfa_disable"
ACTION_API_KEY_CREATE = "api_key_create"
ACTION_API_KEY_DELETE = "api_key_delete"
ACTION_EXPORT_INITIATED = "export_initiated"
ACTION_MEMORY_RESET = "memory_reset"
ACTION_ACCOUNT_DELETION = "account_deletion"
ACTION_OWNERSHIP_VIOLATION = "ownership_violation"
ACTION_RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
ACTION_STRIPE_WEBHOOK = "stripe_webhook"

VALID_ACTIONS: frozenset[str] = frozenset(
    {
        ACTION_LOGIN_SUCCESS,
        ACTION_LOGIN_FAILURE,
        ACTION_LOGOUT,
        ACTION_PASSWORD_CHANGE,
        ACTION_MFA_ENABLE,
        ACTION_MFA_DISABLE,
        ACTION_API_KEY_CREATE,
        ACTION_API_KEY_DELETE,
        ACTION_EXPORT_INITIATED,
        ACTION_MEMORY_RESET,
        ACTION_ACCOUNT_DELETION,
        ACTION_OWNERSHIP_VIOLATION,
        ACTION_RATE_LIMIT_EXCEEDED,
        ACTION_STRIPE_WEBHOOK,
    }
)


class AuditLogger:
    """Append-only security audit logger backed by :class:`SecurityAuditLog`."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def log(
        self,
        *,
        user_id: uuid.UUID | None,
        action: str,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        result: str,
        failure_reason: str | None = None,
        metadata: dict | None = None,
    ) -> SecurityAuditLog:
        """Create an immutable audit log record and flush it to the session.

        The caller is responsible for committing the enclosing transaction.
        """
        entry = SecurityAuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            result=result,
            failure_reason=failure_reason,
            metadata_=metadata,
        )
        self._db.add(entry)
        await self._db.flush()
        return entry

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    async def get_user_audit_log(
        self,
        user_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Return recent audit entries for a given user."""
        stmt = (
            select(SecurityAuditLog)
            .where(SecurityAuditLog.user_id == user_id)
            .order_by(SecurityAuditLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._db.execute(stmt)
        rows = result.scalars().all()
        return [self._row_to_dict(r) for r in rows]

    async def get_failed_logins(
        self,
        ip_address: str,
        minutes: int = 15,
    ) -> int:
        """Count failed login attempts from *ip_address* in the last *minutes*."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        stmt = (
            select(func.count())
            .select_from(SecurityAuditLog)
            .where(
                SecurityAuditLog.action == ACTION_LOGIN_FAILURE,
                SecurityAuditLog.ip_address == ip_address,
                SecurityAuditLog.created_at >= cutoff,
            )
        )
        result = await self._db.execute(stmt)
        return result.scalar_one()

    async def detect_suspicious_activity(
        self,
        user_id: uuid.UUID,
    ) -> list[dict]:
        """Heuristic checks for suspicious behaviour on the given account.

        Returns a list of alert dicts.  Each dict has ``type``, ``detail``,
        and ``timestamp`` keys.
        """
        alerts: list[dict] = []

        # 1. Unusual credit usage — any single day exceeding 3x the user's
        #    daily average over the last 30 days.
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        stmt = text(
            """
            WITH daily AS (
                SELECT DATE(created_at) AS d, COUNT(*) AS cnt
                FROM security_audit_log
                WHERE user_id = :uid
                  AND action IN ('export_initiated')
                  AND created_at >= :cutoff
                GROUP BY DATE(created_at)
            )
            SELECT d, cnt, AVG(cnt) OVER () AS avg_cnt
            FROM daily
            ORDER BY d DESC
            """
        )
        result = await self._db.execute(
            stmt, {"uid": str(user_id), "cutoff": thirty_days_ago}
        )
        for row in result.fetchall():
            day, cnt, avg_cnt = row[0], row[1], float(row[2])
            if avg_cnt > 0 and cnt > avg_cnt * 3:
                alerts.append(
                    {
                        "type": "unusual_usage",
                        "detail": f"Activity count {cnt} on {day} exceeds 3x average ({avg_cnt:.1f})",
                        "timestamp": str(day),
                    }
                )

        # 2. Login from new IP — IPs seen in the last 24 hours that were
        #    never seen in the prior 90 days.
        one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
        ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)
        stmt_new_ip = text(
            """
            SELECT DISTINCT ip_address
            FROM security_audit_log
            WHERE user_id = :uid
              AND action = 'login_success'
              AND created_at >= :recent
              AND ip_address IS NOT NULL
              AND ip_address NOT IN (
                  SELECT DISTINCT ip_address
                  FROM security_audit_log
                  WHERE user_id = :uid
                    AND action = 'login_success'
                    AND created_at >= :older
                    AND created_at < :recent
                    AND ip_address IS NOT NULL
              )
            """
        )
        result = await self._db.execute(
            stmt_new_ip,
            {"uid": str(user_id), "recent": one_day_ago, "older": ninety_days_ago},
        )
        for row in result.fetchall():
            alerts.append(
                {
                    "type": "new_login_ip",
                    "detail": f"Login from previously unseen IP {row[0]}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        # 3. Multiple failed logins followed by a success (credential stuffing
        #    pattern).
        recent_failures = (
            select(func.count())
            .select_from(SecurityAuditLog)
            .where(
                SecurityAuditLog.user_id == user_id,
                SecurityAuditLog.action == ACTION_LOGIN_FAILURE,
                SecurityAuditLog.created_at >= one_day_ago,
            )
        )
        failure_count = (await self._db.execute(recent_failures)).scalar_one()
        if failure_count >= 5:
            alerts.append(
                {
                    "type": "brute_force_attempt",
                    "detail": f"{failure_count} failed login attempts in the last 24 hours",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        return alerts

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: SecurityAuditLog) -> dict:
        return {
            "id": str(row.id),
            "user_id": str(row.user_id) if row.user_id else None,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": str(row.resource_id) if row.resource_id else None,
            "ip_address": row.ip_address,
            "user_agent": row.user_agent,
            "result": row.result,
            "failure_reason": row.failure_reason,
            "metadata": row.metadata_,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
