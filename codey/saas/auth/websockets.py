from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import WebSocket

from codey.saas.auth.cookies import SESSION_COOKIE_NAME
from codey.saas.auth.jwt import decode_access_token, normalize_access_token_candidate


def _websocket_mapping_get(source: object, key: str) -> object | None:
    getter = getattr(source, "get", None)
    if not callable(getter):
        return None
    try:
        return getter(key)
    except Exception:
        return None


def authenticate_websocket(
    websocket: WebSocket,
    token: str | None = None,
) -> dict[str, Any] | None:
    query_params = getattr(websocket, "query_params", None)
    cookies = getattr(websocket, "cookies", None)
    candidate = (
        normalize_access_token_candidate(token)
        or normalize_access_token_candidate(
            _websocket_mapping_get(query_params, "token")
        )
        or normalize_access_token_candidate(
            _websocket_mapping_get(cookies, SESSION_COOKIE_NAME)
        )
    )
    if not candidate:
        return None

    try:
        payload = decode_access_token(candidate)
    except Exception:
        return None

    subject = normalize_access_token_candidate(payload.get("sub"))
    if subject is None:
        return None

    payload["sub"] = subject
    return payload
