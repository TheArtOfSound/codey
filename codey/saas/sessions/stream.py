"""WebSocket endpoint for real-time session output streaming."""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
_URL_CREDENTIAL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&#](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|token|secret)=)[^&#\s]+"
)
_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|token|secret|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


def _redact_stream_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _NAMED_SECRET_RE.sub(r"\1***", text)
    return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)


def _json_safe_stream_value(value: Any, _seen: set[int] | None = None) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if _seen is None:
        _seen = set()
    if isinstance(value, dict):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return {
                str(key): _json_safe_stream_value(item, _seen)
                for key, item in value.items()
            }
        finally:
            _seen.remove(value_id)
    if isinstance(value, (set, frozenset)):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return [
                _json_safe_stream_value(item, _seen)
                for item in sorted(
                    value,
                    key=lambda item: (type(item).__name__, repr(item)),
                )
            ]
        finally:
            _seen.remove(value_id)
    if isinstance(value, (list, tuple)):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return [_json_safe_stream_value(item, _seen) for item in value]
        finally:
            _seen.remove(value_id)
    return str(value)


class SessionStream:
    """Manages WebSocket connections for real-time session output streaming.

    Each coding session can have multiple connected clients (e.g. browser tabs).
    All messages are broadcast to every client subscribed to a given session_id.

    Message types
    -------------
    - ``status``      : Human-readable progress updates.
    - ``nfet_scan``   : Pre-generation NFET sweep results (phase, kappa, sigma, es).
    - ``plan``        : Ordered list of steps the agent will execute.
    - ``code_chunk``  : Incremental code output (file path + content).
    - ``explanation`` : Natural-language description of what was generated.
    - ``nfet_after``  : Post-generation NFET sweep results.
    - ``complete``    : Session finished successfully with final stats.
    - ``error``       : Something went wrong; includes message text.
    """

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, session_id: str, ws: WebSocket) -> None:
        """Accept a WebSocket handshake and register it for *session_id*."""
        await ws.accept()
        if session_id not in self._connections:
            self._connections[session_id] = []
        self._connections[session_id].append(ws)
        logger.info(
            "WebSocket connected for session %s (total: %d)",
            session_id,
            len(self._connections[session_id]),
        )

    async def disconnect(self, session_id: str, ws: WebSocket) -> None:
        """Remove a WebSocket from the session's connection pool."""
        conns = self._connections.get(session_id)
        if conns is None:
            return
        try:
            conns.remove(ws)
        except ValueError:
            pass
        if not conns:
            del self._connections[session_id]
        logger.info("WebSocket disconnected for session %s", session_id)

    async def send_to_session(self, session_id: str, message: dict[str, Any]) -> None:
        """Broadcast a JSON message to every client subscribed to *session_id*.

        Dead connections are silently pruned.
        """
        conns = self._connections.get(session_id)
        if not conns:
            return

        safe_message = _json_safe_stream_value(message)
        stale: list[WebSocket] = []
        for ws in list(conns):
            try:
                await ws.send_json(safe_message)
            except Exception:
                stale.append(ws)

        # Prune dead sockets
        for ws in stale:
            try:
                conns.remove(ws)
            except ValueError:
                pass
        if not conns and self._connections.get(session_id) is conns:
            del self._connections[session_id]


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

session_stream = SessionStream()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.websocket("/sessions/{session_id}/stream")
async def session_websocket(websocket: WebSocket, session_id: str) -> None:
    """WebSocket endpoint for streaming real-time session output.

    Clients connect here and receive JSON messages as the session executes.
    The connection is kept alive until the client disconnects.
    """
    await session_stream.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()  # keep alive
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.warning(
            "Session websocket failed for session %s: %s",
            session_id,
            _redact_stream_error(exc),
        )
        raise
    finally:
        await session_stream.disconnect(session_id, websocket)
