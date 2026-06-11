from __future__ import annotations

from io import BytesIO
import uuid

import pytest
from starlette.datastructures import UploadFile

import codey.saas.api.session_routes as session_routes


class _CurrentUser:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeDB:
    def __init__(self) -> None:
        self.added = None

    def add(self, value) -> None:
        self.added = value

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_create_analyze_session_rejects_empty_file_list_before_billing(monkeypatch) -> None:
    db = _FakeDB()
    user = _CurrentUser()

    async def fail_reserve_credits(self, **kwargs) -> None:
        raise AssertionError("reserve_credits should not be called for empty file lists")

    monkeypatch.setattr(
        session_routes.CreditService,
        "reserve_credits",
        fail_reserve_credits,
    )

    with pytest.raises(session_routes.HTTPException) as exc_info:
        await session_routes.create_analyze_session(
            files=[],
            current_user=user,
            db=db,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "At least one file is required"
    assert db.added is None


@pytest.mark.asyncio
async def test_create_analyze_session_keeps_logical_filenames_only(monkeypatch) -> None:
    db = _FakeDB()
    user = _CurrentUser()

    async def fake_reserve_credits(self, **kwargs) -> None:
        return None

    def fail_mkdtemp(*args, **kwargs):
        raise AssertionError("mkdtemp should not be called")

    monkeypatch.setattr(
        session_routes.CreditService,
        "reserve_credits",
        fake_reserve_credits,
    )
    monkeypatch.setattr(session_routes.tempfile, "mkdtemp", fail_mkdtemp)

    response = await session_routes.create_analyze_session(
        files=[UploadFile(filename="../demo.py", file=BytesIO(b"print('ok')"))],
        current_user=user,
        db=db,
    )

    assert response.session_id
    assert db.added is not None
    assert db.added.files_uploaded == ["demo.py"]


@pytest.mark.asyncio
async def test_create_analyze_session_does_not_charge_without_worker(monkeypatch) -> None:
    db = _FakeDB()
    user = _CurrentUser()

    async def fail_reserve_credits(self, **kwargs) -> None:
        raise AssertionError("reserve_credits should not run without an analysis worker")

    monkeypatch.setattr(
        session_routes.CreditService,
        "reserve_credits",
        fail_reserve_credits,
    )

    response = await session_routes.create_analyze_session(
        files=[UploadFile(filename="demo.py", file=BytesIO(b"print('ok')"))],
        current_user=user,
        db=db,
    )

    assert response.session_id
    assert db.added.status == "completed"
    assert db.added.credits_charged == 0
    assert db.added.completed_at is not None


@pytest.mark.asyncio
async def test_create_analyze_session_normalizes_windows_style_filenames(
    monkeypatch,
) -> None:
    db = _FakeDB()
    user = _CurrentUser()

    async def fake_reserve_credits(self, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        session_routes.CreditService,
        "reserve_credits",
        fake_reserve_credits,
    )

    response = await session_routes.create_analyze_session(
        files=[UploadFile(filename="..\\demo.py", file=BytesIO(b"print('ok')"))],
        current_user=user,
        db=db,
    )

    assert response.session_id
    assert db.added is not None
    assert db.added.files_uploaded == ["demo.py"]


@pytest.mark.asyncio
async def test_create_analyze_session_normalizes_blank_filenames(monkeypatch) -> None:
    db = _FakeDB()
    user = _CurrentUser()

    async def fake_reserve_credits(self, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        session_routes.CreditService,
        "reserve_credits",
        fake_reserve_credits,
    )
    monkeypatch.setattr(
        session_routes.uuid,
        "uuid4",
        lambda: uuid.UUID("12345678-1234-5678-1234-567812345678"),
    )

    response = await session_routes.create_analyze_session(
        files=[UploadFile(filename="   ", file=BytesIO(b"print('ok')"))],
        current_user=user,
        db=db,
    )

    assert response.session_id
    assert db.added is not None
    assert db.added.files_uploaded == ["file_12345678"]


@pytest.mark.asyncio
async def test_create_analyze_session_normalizes_control_character_filenames(
    monkeypatch,
) -> None:
    db = _FakeDB()
    user = _CurrentUser()

    async def fake_reserve_credits(self, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        session_routes.CreditService,
        "reserve_credits",
        fake_reserve_credits,
    )
    monkeypatch.setattr(
        session_routes.uuid,
        "uuid4",
        lambda: uuid.UUID("12345678-1234-5678-1234-567812345678"),
    )

    response = await session_routes.create_analyze_session(
        files=[UploadFile(filename="bad\nname.py", file=BytesIO(b"print('ok')"))],
        current_user=user,
        db=db,
    )

    assert response.session_id
    assert db.added is not None
    assert db.added.files_uploaded == ["file_12345678"]


@pytest.mark.asyncio
async def test_create_analyze_session_normalizes_directory_only_filenames(monkeypatch) -> None:
    db = _FakeDB()
    user = _CurrentUser()

    async def fake_reserve_credits(self, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        session_routes.CreditService,
        "reserve_credits",
        fake_reserve_credits,
    )
    monkeypatch.setattr(
        session_routes.uuid,
        "uuid4",
        lambda: uuid.UUID("12345678-1234-5678-1234-567812345678"),
    )

    response = await session_routes.create_analyze_session(
        files=[UploadFile(filename="../", file=BytesIO(b"print('ok')"))],
        current_user=user,
        db=db,
    )

    assert response.session_id
    assert db.added is not None
    assert db.added.files_uploaded == ["file_12345678"]
