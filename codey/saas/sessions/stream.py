"""WebSocket endpoint for real-time session output streaming."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


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

        stale: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)

        # Prune dead sockets
        for ws in stale:
            try:
                conns.remove(ws)
            except ValueError:
                pass
        if not conns:
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
        await session_stream.disconnect(session_id, websocket)
