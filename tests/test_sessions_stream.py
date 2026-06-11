from __future__ import annotations

import json
import logging

import pytest

from fastapi import WebSocketDisconnect

import codey.saas.sessions.stream as stream_module


class _FakeWebSocket:
    def __init__(self, incoming: list[str | Exception]) -> None:
        self.accepted = False
        self._incoming = list(incoming)

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        if not self._incoming:
            raise WebSocketDisconnect()
        item = self._incoming.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _MutatingSendWebSocket:
    def __init__(self, stream: stream_module.SessionStream, to_remove=None) -> None:
        self.stream = stream
        self.to_remove = to_remove
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        if self.to_remove is not None:
            self.stream._connections["session-1"].remove(self.to_remove)
        self.messages.append(data)


class _DeletingFailingWebSocket:
    def __init__(self, stream: stream_module.SessionStream) -> None:
        self.stream = stream

    async def send_json(self, data: dict) -> None:
        del self.stream._connections["session-1"]
        raise RuntimeError("connection closed")


@pytest.mark.asyncio
async def test_session_websocket_cleans_up_connection_after_unexpected_error(
    monkeypatch,
    caplog,
) -> None:
    session_stream = stream_module.SessionStream()
    websocket = _FakeWebSocket(
        [
            RuntimeError(
                "boom https://user:url-secret@example.test/ws"
                "?token=ws-token&client_secret=query-client-secret "
                "mirror=https://example.test/ws#client_secret=fragment-secret "
                "authorization: Bearer ws-auth access_token=access-secret "
                "auth_token=auth-secret refresh_token=refresh-secret "
                "password=inline-password "
                "for user@example.com"
            )
        ]
    )

    monkeypatch.setattr(stream_module, "session_stream", session_stream)
    caplog.set_level(logging.WARNING, logger="codey.saas.sessions.stream")

    with pytest.raises(RuntimeError, match="boom"):
        await stream_module.session_websocket(websocket, "session-1")

    assert websocket.accepted is True
    assert "session-1" not in session_stream._connections
    assert "url-secret" not in caplog.text
    assert "ws-token" not in caplog.text
    assert "query-client-secret" not in caplog.text
    assert "fragment-secret" not in caplog.text
    assert "ws-auth" not in caplog.text
    assert "access-secret" not in caplog.text
    assert "auth-secret" not in caplog.text
    assert "refresh-secret" not in caplog.text
    assert "inline-password" not in caplog.text
    assert "user@example.com" not in caplog.text
    assert "https://***@example.test/ws?token=***&client_secret=***" in caplog.text
    assert "authorization: Bearer ***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "auth_token=***" in caplog.text
    assert "refresh_token=***" in caplog.text
    assert "password=***" in caplog.text
    assert "***@example.com" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_send_to_session_uses_connection_snapshot() -> None:
    session_stream = stream_module.SessionStream()
    second = _MutatingSendWebSocket(session_stream)
    first = _MutatingSendWebSocket(session_stream, to_remove=second)
    session_stream._connections["session-1"] = [first, second]

    await session_stream.send_to_session("session-1", {"type": "status"})

    assert first.messages == [{"type": "status"}]
    assert second.messages == [{"type": "status"}]
    assert session_stream._connections["session-1"] == [first]


@pytest.mark.asyncio
async def test_send_to_session_sanitizes_json_payload_for_all_clients() -> None:
    session_stream = stream_module.SessionStream()
    websocket = _MutatingSendWebSocket(session_stream)
    session_stream._connections["session-1"] = [websocket]

    await session_stream.send_to_session(
        "session-1",
        {
            "type": "metrics",
            "score": float("inf"),
            "nested": (float("nan"),),
            "set_metric": {float("inf")},
        },
    )

    assert websocket.messages == [
        {
            "type": "metrics",
            "score": 0.0,
            "nested": [0.0],
            "set_metric": [0.0],
        }
    ]
    json.dumps(websocket.messages[0], allow_nan=False)


def test_json_safe_stream_value_sorts_sets_deterministically() -> None:
    value = stream_module._json_safe_stream_value({"items": {"b", "a", 3}})

    assert value == {"items": [3, "a", "b"]}


@pytest.mark.asyncio
async def test_send_to_session_sanitizes_cyclic_json_payloads() -> None:
    session_stream = stream_module.SessionStream()
    websocket = _MutatingSendWebSocket(session_stream)
    session_stream._connections["session-1"] = [websocket]
    cycle: dict[str, object] = {"type": "metrics"}
    cycle["self"] = cycle

    await session_stream.send_to_session("session-1", cycle)

    assert websocket.messages == [
        {
            "type": "metrics",
            "self": "[Circular]",
        }
    ]
    json.dumps(websocket.messages[0], allow_nan=False)


@pytest.mark.asyncio
async def test_send_to_session_ignores_stale_session_entry_deleted_during_send() -> None:
    session_stream = stream_module.SessionStream()
    websocket = _DeletingFailingWebSocket(session_stream)
    session_stream._connections["session-1"] = [websocket]

    await session_stream.send_to_session("session-1", {"type": "status"})

    assert "session-1" not in session_stream._connections
