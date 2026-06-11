from __future__ import annotations

import json
import uuid

import pytest
from fastapi import WebSocketDisconnect

import codey.saas.api.session_routes as session_routes
from codey.saas.models import CodingSession


class _ScalarResult:
    def __init__(self, session: CodingSession | None) -> None:
        self._session = session

    def scalar_one_or_none(self) -> CodingSession | None:
        return self._session


class _FakeSession:
    def __init__(self, session: CodingSession | None) -> None:
        self._session = session

    async def execute(self, stmt):
        return _ScalarResult(self._session)


class _FakeStackResult:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeStack:
    async def run(self, prompt, messages, context):
        return _FakeStackResult("print('ok')\n")


class _MissingContentStack:
    async def run(self, prompt, messages, context):
        return object()


class _LeakyErrorStack:
    async def run(self, prompt, messages, context):
        raise RuntimeError("provider failed https://user:secret@example.test/repo")


class _FakeWebSocket:
    def __init__(self, incoming: list[str | Exception]) -> None:
        self.accepted = False
        self.closed = False
        self.sent: list[dict] = []
        self._incoming = list(incoming)

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = True

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
async def test_send_session_stream_json_sanitizes_non_finite_payload() -> None:
    websocket = _FakeWebSocket([])

    await session_routes._send_session_stream_json(
        websocket,
        {
            "type": "health_after",
            "score": float("inf"),
            "nested": (float("nan"),),
            "set_metric": {float("inf")},
        },
    )

    assert websocket.sent == [
        {
            "type": "health_after",
            "score": 0.0,
            "nested": [0.0],
            "set_metric": [0.0],
        }
    ]
    json.dumps(websocket.sent[0], allow_nan=False)


@pytest.mark.asyncio
async def test_send_session_stream_json_sanitizes_cyclic_payload() -> None:
    websocket = _FakeWebSocket([])
    cycle: dict[str, object] = {"type": "health_after"}
    cycle["self"] = cycle

    await session_routes._send_session_stream_json(websocket, cycle)

    assert websocket.sent == [
        {
            "type": "health_after",
            "self": "[Circular]",
        }
    ]
    json.dumps(websocket.sent[0], allow_nan=False)


def test_json_safe_session_stream_value_sorts_sets_deterministically() -> None:
    value = session_routes._json_safe_session_stream_value({"items": {"b", "a", 3}})

    assert value == {"items": [3, "a", "b"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("incoming", ["{", "[]"])
async def test_stream_session_rejects_invalid_auth_payload(
    monkeypatch, incoming: str
) -> None:
    websocket = _FakeWebSocket([incoming])

    authenticate_calls = []

    def fake_authenticate(websocket_obj, token):
        authenticate_calls.append((websocket_obj, token))
        return {"sub": str(uuid.uuid4())}

    monkeypatch.setattr(session_routes, "authenticate_websocket", fake_authenticate)

    await session_routes.stream_session(websocket, str(uuid.uuid4()))

    assert websocket.accepted is True
    assert websocket.closed is True
    assert websocket.sent[0] == {
        "type": "error",
        "message": "Invalid authentication payload",
    }
    assert authenticate_calls == []


def test_coerce_stream_auth_payload_trims_prompt_and_defaults_blank_language() -> None:
    payload = session_routes._coerce_stream_auth_payload(
        json.dumps(
            {
                "token": " token ",
                "prompt": "   print('ok')   ",
                "language": "   ",
            }
        )
    )

    assert payload == {
        "token": "token",
        "prompt": "print('ok')",
        "language": "python",
    }


@pytest.mark.asyncio
async def test_stream_session_rejects_blank_prompt_after_normalization(monkeypatch) -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    websocket = _FakeWebSocket(
        [json.dumps({"token": "token", "prompt": "   ", "language": "python"})]
    )
    db_calls = 0

    async def fake_get_db():
        nonlocal db_calls
        db_calls += 1
        yield _FakeSession(session=None)

    monkeypatch.setattr(
        session_routes,
        "authenticate_websocket",
        lambda websocket, token: {"sub": str(user_id)},
    )
    monkeypatch.setattr(session_routes, "get_db", fake_get_db)

    await session_routes.stream_session(websocket, str(session_id))

    assert websocket.accepted is True
    assert websocket.closed is True
    assert websocket.sent[0] == {"type": "error", "message": "No prompt provided"}
    assert db_calls == 0


@pytest.mark.asyncio
async def test_stream_session_rejects_unowned_session(monkeypatch) -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    websocket = _FakeWebSocket(
        [json.dumps({"token": "token", "prompt": "print('ok')", "language": "python"})]
    )

    async def fake_get_db():
        yield _FakeSession(session=None)

    monkeypatch.setattr(
        session_routes,
        "authenticate_websocket",
        lambda websocket, token: {"sub": str(user_id)},
    )
    monkeypatch.setattr(session_routes, "get_db", fake_get_db)

    await session_routes.stream_session(websocket, str(session_id))

    assert websocket.accepted is True
    assert websocket.closed is True
    assert websocket.sent[0] == {"type": "error", "message": "Session not found"}


@pytest.mark.asyncio
async def test_stream_session_accepts_owned_session(monkeypatch) -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    websocket = _FakeWebSocket(
        [json.dumps({"token": "token", "prompt": "print('ok')", "language": "python"})]
    )
    session = CodingSession(id=session_id, user_id=user_id, mode="prompt")

    async def fake_get_db():
        yield _FakeSession(session=session)

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(
        session_routes,
        "authenticate_websocket",
        lambda websocket, token: {"sub": str(user_id)},
    )
    monkeypatch.setattr(session_routes, "get_db", fake_get_db)
    monkeypatch.setattr(session_routes, "IntelligenceStack", lambda: _FakeStack())
    monkeypatch.setattr(session_routes.asyncio, "sleep", fake_sleep)

    await session_routes.stream_session(websocket, str(session_id))

    message_types = [message["type"] for message in websocket.sent]
    assert websocket.accepted is True
    assert "status" in message_types
    assert "code_chunk" in message_types
    assert message_types[-1] == "complete"
    assert websocket.sent[-1]["lines_generated"] == 1


@pytest.mark.asyncio
async def test_stream_session_reports_missing_model_content(monkeypatch) -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    websocket = _FakeWebSocket(
        [json.dumps({"token": "token", "prompt": "print('ok')", "language": "python"})]
    )
    session = CodingSession(id=session_id, user_id=user_id, mode="prompt")

    async def fake_get_db():
        yield _FakeSession(session=session)

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(
        session_routes,
        "authenticate_websocket",
        lambda websocket, token: {"sub": str(user_id)},
    )
    monkeypatch.setattr(session_routes, "get_db", fake_get_db)
    monkeypatch.setattr(session_routes, "IntelligenceStack", lambda: _MissingContentStack())
    monkeypatch.setattr(session_routes.asyncio, "sleep", fake_sleep)

    await session_routes.stream_session(websocket, str(session_id))

    assert websocket.accepted is True
    assert websocket.closed is True
    assert websocket.sent[-1] == {
        "type": "error",
        "message": "Intelligence stack returned non-text prompt output",
    }


@pytest.mark.asyncio
async def test_stream_session_redacts_credentials_from_error_messages(monkeypatch) -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    websocket = _FakeWebSocket(
        [json.dumps({"token": "token", "prompt": "print('ok')", "language": "python"})]
    )
    session = CodingSession(id=session_id, user_id=user_id, mode="prompt")

    async def fake_get_db():
        yield _FakeSession(session=session)

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(
        session_routes,
        "authenticate_websocket",
        lambda websocket, token: {"sub": str(user_id)},
    )
    monkeypatch.setattr(session_routes, "get_db", fake_get_db)
    monkeypatch.setattr(session_routes, "IntelligenceStack", lambda: _LeakyErrorStack())
    monkeypatch.setattr(session_routes.asyncio, "sleep", fake_sleep)

    await session_routes.stream_session(websocket, str(session_id))

    assert websocket.accepted is True
    assert websocket.closed is True
    assert websocket.sent[-1]["type"] == "error"
    assert "secret" not in websocket.sent[-1]["message"]
    assert "https://***@example.test/repo" in websocket.sent[-1]["message"]
