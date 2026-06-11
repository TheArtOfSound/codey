from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import codey.saas.api.session_routes as session_routes


class _FakeDB:
    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_commit_session_rejects_sessions_without_repo_before_billing(monkeypatch) -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return SimpleNamespace(
            id=session_id,
            status="completed",
            repo_connected=None,
            credits_charged=3,
        )

    class _FailingCreditService:
        def __init__(self, db) -> None:
            self.db = db

        async def reserve_credits(self, **kwargs) -> None:
            raise AssertionError("reserve_credits should not run without a connected repo")

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )
    monkeypatch.setattr(session_routes, "CreditService", _FailingCreditService)

    with pytest.raises(session_routes.HTTPException) as exc_info:
        await session_routes.commit_session(
            str(session_id),
            current_user=SimpleNamespace(id=user_id),
            db=_FakeDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Session is not connected to a repository"


@pytest.mark.asyncio
async def test_commit_session_rejects_sessions_missing_repo_attribute_before_billing(
    monkeypatch,
) -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return SimpleNamespace(
            id=session_id,
            status="completed",
            credits_charged=3,
        )

    class _FailingCreditService:
        def __init__(self, db) -> None:
            self.db = db

        async def reserve_credits(self, **kwargs) -> None:
            raise AssertionError("reserve_credits should not run without a connected repo")

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )
    monkeypatch.setattr(session_routes, "CreditService", _FailingCreditService)

    with pytest.raises(session_routes.HTTPException) as exc_info:
        await session_routes.commit_session(
            str(session_id),
            current_user=SimpleNamespace(id=user_id, github_token="gh-token"),
            db=_FakeDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Session is not connected to a repository"


@pytest.mark.asyncio
async def test_commit_session_requires_github_auth_before_billing(monkeypatch) -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return SimpleNamespace(
            id=session_id,
            status="completed",
            repo_connected="owner/repo",
            credits_charged=3,
        )

    class _FailingCreditService:
        def __init__(self, db) -> None:
            self.db = db

        async def reserve_credits(self, **kwargs) -> None:
            raise AssertionError("reserve_credits should not run without GitHub auth")

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )
    monkeypatch.setattr(session_routes, "CreditService", _FailingCreditService)

    with pytest.raises(session_routes.HTTPException) as exc_info:
        await session_routes.commit_session(
            str(session_id),
            current_user=SimpleNamespace(id=user_id, github_token=None),
            db=_FakeDB(),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "GitHub authentication required to commit code"


@pytest.mark.asyncio
async def test_commit_session_requires_github_auth_when_token_attribute_missing(
    monkeypatch,
) -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return SimpleNamespace(
            id=session_id,
            status="completed",
            repo_connected="owner/repo",
            credits_charged=3,
        )

    class _FailingCreditService:
        def __init__(self, db) -> None:
            self.db = db

        async def reserve_credits(self, **kwargs) -> None:
            raise AssertionError("reserve_credits should not run without GitHub auth")

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )
    monkeypatch.setattr(session_routes, "CreditService", _FailingCreditService)

    with pytest.raises(session_routes.HTTPException) as exc_info:
        await session_routes.commit_session(
            str(session_id),
            current_user=SimpleNamespace(id=user_id),
            db=_FakeDB(),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "GitHub authentication required to commit code"


@pytest.mark.asyncio
async def test_commit_session_rejects_malformed_github_token_before_billing(monkeypatch) -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return SimpleNamespace(
            id=session_id,
            status="completed",
            repo_connected="owner/repo",
            credits_charged=3,
        )

    class _FailingCreditService:
        def __init__(self, db) -> None:
            self.db = db

        async def reserve_credits(self, **kwargs) -> None:
            raise AssertionError("reserve_credits should not run without a valid GitHub token")

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )
    monkeypatch.setattr(session_routes, "CreditService", _FailingCreditService)

    with pytest.raises(session_routes.HTTPException) as exc_info:
        await session_routes.commit_session(
            str(session_id),
            current_user=SimpleNamespace(id=user_id, github_token={"token": "gh-token"}),
            db=_FakeDB(),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "GitHub authentication required to commit code"


@pytest.mark.asyncio
async def test_commit_session_rejects_ascii_control_github_token_before_billing(
    monkeypatch,
) -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return SimpleNamespace(
            id=session_id,
            status="completed",
            repo_connected="owner/repo",
            credits_charged=3,
        )

    class _FailingCreditService:
        def __init__(self, db) -> None:
            self.db = db

        async def reserve_credits(self, **kwargs) -> None:
            raise AssertionError("reserve_credits should not run without a valid GitHub token")

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )
    monkeypatch.setattr(session_routes, "CreditService", _FailingCreditService)

    with pytest.raises(session_routes.HTTPException) as exc_info:
        await session_routes.commit_session(
            str(session_id),
            current_user=SimpleNamespace(id=user_id, github_token="gh-token\tbad"),
            db=_FakeDB(),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "GitHub authentication required to commit code"


@pytest.mark.asyncio
async def test_commit_session_passes_session_uuid_to_credit_reservation(monkeypatch) -> None:
    reserved: list[uuid.UUID] = []
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return SimpleNamespace(
            id=session_id,
            status="completed",
            repo_connected="owner/repo",
            credits_charged=3,
        )

    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        async def reserve_credits(
            self,
            user_id,
            estimated_cost,
            description,
            session_id=None,
        ) -> None:
            reserved.append(session_id)

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )
    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)

    response = await session_routes.commit_session(
        str(session_id),
        current_user=SimpleNamespace(id=user_id, github_token="gh-token"),
        db=_FakeDB(),
    )

    assert reserved == [session_id]
    assert response.session_id == str(session_id)
    assert response.credits_charged == 4


@pytest.mark.asyncio
async def test_commit_session_coerces_legacy_string_credit_totals(monkeypatch) -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = SimpleNamespace(
        id=session_id,
        status="completed",
        repo_connected="owner/repo",
        credits_charged="3",
    )

    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return session

    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        async def reserve_credits(
            self,
            user_id,
            estimated_cost,
            description,
            session_id=None,
        ) -> None:
            return None

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )
    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)

    response = await session_routes.commit_session(
        str(session_id),
        current_user=SimpleNamespace(id=user_id, github_token="gh-token"),
        db=_FakeDB(),
    )

    assert session.credits_charged == 4
    assert response.credits_charged == 4


@pytest.mark.asyncio
async def test_commit_session_defaults_missing_credit_totals(monkeypatch) -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = SimpleNamespace(
        id=session_id,
        status="completed",
        repo_connected="owner/repo",
    )

    async def fake_get_session_for_user_id(session_id_str, user_id_arg, db):
        return session

    class _FakeCreditService:
        def __init__(self, db) -> None:
            self.db = db

        async def reserve_credits(
            self,
            user_id,
            estimated_cost,
            description,
            session_id=None,
        ) -> None:
            return None

    monkeypatch.setattr(
        session_routes,
        "_get_session_for_user_id",
        fake_get_session_for_user_id,
    )
    monkeypatch.setattr(session_routes, "CreditService", _FakeCreditService)

    response = await session_routes.commit_session(
        str(session_id),
        current_user=SimpleNamespace(id=user_id, github_token="gh-token"),
        db=_FakeDB(),
    )

    assert session.credits_charged == 1
    assert response.credits_charged == 1
