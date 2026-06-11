from __future__ import annotations

import uuid

import pytest
from starlette.requests import Request

import codey.saas.security.ownership as ownership
from codey.saas.auth.cookies import SESSION_COOKIE_NAME
from codey.saas.auth.jwt import create_access_token


def _make_request(resource_id: uuid.UUID, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/sessions/{resource_id}",
            "headers": headers or [],
            "query_string": b"",
            "scheme": "https",
            "server": ("testserver", 443),
            "client": ("127.0.0.1", 1234),
            "path_params": {"resource_id": str(resource_id)},
        }
    )


def _make_request_with_resource_id(
    resource_id: str | None,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    path_params = {}
    if resource_id is not None:
        path_params["resource_id"] = resource_id
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/sessions/{resource_id or ''}",
            "headers": headers or [],
            "query_string": b"",
            "scheme": "https",
            "server": ("testserver", 443),
            "client": ("127.0.0.1", 1234),
            "path_params": path_params,
        }
    )


def test_request_user_id_accepts_loose_bearer_header_and_trimmed_subject(monkeypatch) -> None:
    user_id = uuid.uuid4()
    request = _make_request(
        uuid.uuid4(),
        headers=[(b"authorization", b"  bearer   access-token  ")],
    )

    def fake_decode_access_token(token: str) -> dict[str, str]:
        assert token == "access-token"
        return {"sub": f" {user_id} "}

    monkeypatch.setattr("codey.saas.auth.jwt.decode_access_token", fake_decode_access_token)

    assert ownership._request_user_id(request) == user_id


@pytest.mark.asyncio
async def test_require_ownership_accepts_session_cookie_token(monkeypatch) -> None:
    user_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    request = _make_request(
        resource_id,
        headers=[(b"cookie", f"{SESSION_COOKIE_NAME}={create_access_token(str(user_id))}".encode())],
    )
    captured: dict[str, object] = {}
    db = object()

    async def fake_set_db_user_context(db_session, user_id_text: str) -> None:
        captured["db"] = db_session
        captured["db_user_id"] = user_id_text

    async def fake_verify_ownership(
        resolved_user_id: uuid.UUID,
        resolved_resource_id: uuid.UUID,
        resource_type: str,
        db_session,
    ) -> bool:
        captured["user_id"] = resolved_user_id
        captured["resource_id"] = resolved_resource_id
        captured["resource_type"] = resource_type
        captured["verify_db"] = db_session
        return True

    monkeypatch.setattr(ownership, "set_db_user_context", fake_set_db_user_context)
    monkeypatch.setattr(ownership, "verify_ownership", fake_verify_ownership)

    dependency = ownership.require_ownership("session")
    allowed = await dependency(request, db=db)

    assert allowed is True
    assert captured == {
        "db": db,
        "db_user_id": str(user_id),
        "user_id": user_id,
        "resource_id": resource_id,
        "resource_type": "session",
        "verify_db": db,
    }


@pytest.mark.asyncio
async def test_require_ownership_rejects_invalid_resource_id(monkeypatch) -> None:
    user_id = uuid.uuid4()
    request = _make_request_with_resource_id(
        "not-a-uuid",
        headers=[(b"cookie", f"{SESSION_COOKIE_NAME}={create_access_token(str(user_id))}".encode())],
    )
    db = object()

    async def fake_set_db_user_context(db_session, user_id_text: str) -> None:
        return None

    async def fake_verify_ownership(*args, **kwargs) -> bool:
        raise AssertionError("verify_ownership should not be called for invalid IDs")

    monkeypatch.setattr(ownership, "set_db_user_context", fake_set_db_user_context)
    monkeypatch.setattr(ownership, "verify_ownership", fake_verify_ownership)

    dependency = ownership.require_ownership("session")
    with pytest.raises(ownership.HTTPException) as exc_info:
        await dependency(request, db=db)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid resource_id path parameter"
