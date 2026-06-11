from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import codey.saas.api.billing_routes as billing_routes


class _FakeRequest:
    def __init__(self, payload: bytes = b"{}", signature: str = "sig") -> None:
        self._payload = payload
        self.headers = {"stripe-signature": signature}

    async def body(self) -> bytes:
        return self._payload


def test_stripe_webhook_returns_400_for_signature_failures(monkeypatch) -> None:
    async def fake_handle(_payload, _sig_header, _db):
        raise ValueError("invalid payload")

    monkeypatch.setattr(billing_routes, "handle_stripe_webhook", fake_handle)

    with pytest.raises(HTTPException, match="Webhook signature verification failed") as excinfo:
        asyncio.run(billing_routes.stripe_webhook(_FakeRequest(), db=object()))

    assert excinfo.value.status_code == 400


def test_stripe_webhook_preserves_internal_processing_errors(monkeypatch) -> None:
    async def fake_handle(_payload, _sig_header, _db):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(billing_routes, "handle_stripe_webhook", fake_handle)

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(billing_routes.stripe_webhook(_FakeRequest(), db=object()))


def test_stripe_webhook_trims_whitespace_signature_header(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_handle(payload, sig_header, db):
        captured["payload"] = payload
        captured["sig_header"] = sig_header
        captured["db"] = db
        return {"status": "ok"}

    monkeypatch.setattr(billing_routes, "handle_stripe_webhook", fake_handle)

    response = asyncio.run(
        billing_routes.stripe_webhook(
            _FakeRequest(payload=b'{"id":"evt_123"}', signature=" sig-value "),
            db="db-session",
        )
    )

    assert response.status == "ok"
    assert captured == {
        "payload": b'{"id":"evt_123"}',
        "sig_header": "sig-value",
        "db": "db-session",
    }


def test_stripe_webhook_rejects_whitespace_signature_header() -> None:
    with pytest.raises(HTTPException, match="Missing Stripe signature header") as excinfo:
        asyncio.run(
            billing_routes.stripe_webhook(
                _FakeRequest(signature="   "),
                db=object(),
            )
        )

    assert excinfo.value.status_code == 400
