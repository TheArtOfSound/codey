from __future__ import annotations

import json
import uuid

import pytest
from fastapi import WebSocketDisconnect

import codey.saas.api.build_routes as build_routes
from codey.saas.models import BuildProject


class _ScalarResult:
    def __init__(self, project: BuildProject | None) -> None:
        self._project = project

    def scalar_one_or_none(self) -> BuildProject | None:
        return self._project


class _FakeSession:
    def __init__(self, project: BuildProject | None) -> None:
        self._project = project

    async def execute(self, stmt):
        return _ScalarResult(self._project)


class _FakeWebSocket:
    def __init__(self, incoming: list[str | Exception] | None = None) -> None:
        self.accepted = False
        self.closed: tuple[int, str] | None = None
        self.sent: list[dict] = []
        self._incoming = list(incoming or [])

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason or "")

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def receive_text(self) -> str:
        if not self._incoming:
            raise WebSocketDisconnect()
        item = self._incoming.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.mark.asyncio
async def test_send_build_stream_json_sanitizes_non_finite_payload() -> None:
    websocket = _FakeWebSocket()

    await build_routes._send_build_stream_json(
        websocket,
        {
            "type": "nfet",
            "data": {
                "score": float("inf"),
                "nested": (float("nan"),),
                "set_metric": {float("inf")},
            },
        },
    )

    assert websocket.sent == [
        {
            "type": "nfet",
            "data": {
                "score": 0.0,
                "nested": [0.0],
                "set_metric": [0.0],
            },
        }
    ]
    json.dumps(websocket.sent[0], allow_nan=False)


@pytest.mark.asyncio
async def test_send_build_stream_json_sanitizes_cyclic_payload() -> None:
    websocket = _FakeWebSocket()
    cycle: dict[str, object] = {"type": "nfet"}
    cycle["self"] = cycle

    await build_routes._send_build_stream_json(websocket, cycle)

    assert websocket.sent == [
        {
            "type": "nfet",
            "self": "[Circular]",
        }
    ]
    json.dumps(websocket.sent[0], allow_nan=False)


def test_json_safe_build_stream_value_sorts_sets_deterministically() -> None:
    value = build_routes._json_safe_build_stream_value({"items": {"b", "a", 3}})

    assert value == {"items": [3, "a", "b"]}


@pytest.mark.asyncio
async def test_build_stream_rejects_unowned_project_before_accept(monkeypatch) -> None:
    user_id = uuid.uuid4()
    websocket = _FakeWebSocket()

    async def fake_get_db():
        yield _FakeSession(project=None)

    monkeypatch.setattr(
        build_routes,
        "authenticate_websocket",
        lambda websocket, token: {"sub": str(user_id)},
    )
    monkeypatch.setattr(build_routes, "get_db", fake_get_db)

    await build_routes.build_stream(websocket, str(uuid.uuid4()), token="token")

    assert websocket.accepted is False
    assert websocket.closed == (1008, "Build project not found")


@pytest.mark.asyncio
async def test_build_stream_accepts_owned_project_and_replies_to_ping(monkeypatch) -> None:
    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    websocket = _FakeWebSocket(incoming=['{"type":"ping"}'])
    project = BuildProject(id=project_id, user_id=user_id)

    async def fake_get_db():
        yield _FakeSession(project=project)

    monkeypatch.setattr(
        build_routes,
        "authenticate_websocket",
        lambda websocket, token: {"sub": str(user_id)},
    )
    monkeypatch.setattr(build_routes, "get_db", fake_get_db)

    await build_routes.build_stream(websocket, str(project_id), token="token")

    assert websocket.accepted is True
    assert websocket.closed is None
    assert websocket.sent[0]["type"] == "status"
    assert websocket.sent[1]["type"] == "pong"


@pytest.mark.asyncio
async def test_build_stream_ignores_valid_non_object_json_messages(monkeypatch) -> None:
    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    websocket = _FakeWebSocket(incoming=["[]"])
    project = BuildProject(id=project_id, user_id=user_id)

    async def fake_get_db():
        yield _FakeSession(project=project)

    monkeypatch.setattr(
        build_routes,
        "authenticate_websocket",
        lambda websocket, token: {"sub": str(user_id)},
    )
    monkeypatch.setattr(build_routes, "get_db", fake_get_db)

    await build_routes.build_stream(websocket, str(project_id), token="token")

    assert websocket.accepted is True
    assert websocket.closed is None
    assert [message["type"] for message in websocket.sent] == ["status"]
