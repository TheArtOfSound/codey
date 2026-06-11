from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, status
from pydantic import ValidationError

import codey.saas.api.memory_routes as memory_routes


class _FakeDB:
    def __init__(self) -> None:
        self.flush_calls = 0

    async def flush(self) -> None:
        self.flush_calls += 1


class _TimelineResult:
    def __init__(self, logs) -> None:
        self._logs = logs

    def scalars(self):
        return self

    def all(self):
        return self._logs


class _TimelineDB:
    def __init__(self, logs) -> None:
        self._logs = logs

    async def execute(self, _statement):
        return _TimelineResult(self._logs)


def test_create_memory_item_request_rejects_blank_keys_and_values() -> None:
    with pytest.raises(ValidationError):
        memory_routes.CreateMemoryItemRequest(
            dimension="coding_style",
            key="   ",
            value="concise",
        )

    with pytest.raises(ValidationError):
        memory_routes.CreateMemoryItemRequest(
            dimension="coding_style",
            key="verbosity",
            value="   ",
        )


def test_create_memory_item_request_rejects_control_character_keys() -> None:
    with pytest.raises(ValidationError):
        memory_routes.CreateMemoryItemRequest(
            dimension="coding_style",
            key="verbosity\nbad",
            value="concise",
        )


def test_decode_id_rejects_control_character_keys() -> None:
    item_id = memory_routes._encode_id("style_model", "verbosity\nbad")

    with pytest.raises(HTTPException) as exc_info:
        memory_routes._decode_id(item_id)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid memory item id"


def test_update_memory_item_request_rejects_blank_values() -> None:
    with pytest.raises(ValidationError):
        memory_routes.UpdateMemoryItemRequest(value="   ")


def test_flatten_memory_tolerates_string_last_updated() -> None:
    memory = SimpleNamespace(
        style_model={"verbosity": "concise"},
        work_patterns={},
        project_knowledge={},
        communication_style={},
        structural_preferences={},
        skill_profile={},
        explicit_preferences=[],
        last_updated=" 2026-01-02T03:04:05Z ",
    )

    items = memory_routes._flatten_memory(memory)

    assert len(items) == 1
    assert items[0].updated_at == "2026-01-02T03:04:05Z"


def test_flatten_memory_ignores_malformed_buckets_and_preferences() -> None:
    memory = SimpleNamespace(
        style_model="oops",
        work_patterns='{"window": "morning"}',
        project_knowledge=[],
        communication_style=None,
        structural_preferences=0,
        skill_profile="[]",
        explicit_preferences="oops",
        last_updated=datetime(2026, 1, 2, 3, 4, 5),
    )

    items = memory_routes._flatten_memory(memory)

    assert len(items) == 1
    assert items[0].key == "window"
    assert items[0].value == "morning"


def test_flatten_memory_tolerates_missing_legacy_fields() -> None:
    items = memory_routes._flatten_memory(SimpleNamespace())

    assert items == []


def test_flatten_memory_skips_control_character_keys() -> None:
    memory = SimpleNamespace(
        style_model={"verbosity\nbad": "concise", "verbosity": "detailed"},
        work_patterns={},
        project_knowledge={},
        communication_style={},
        structural_preferences={},
        skill_profile={},
        explicit_preferences=[],
        last_updated=datetime(2026, 1, 2, 3, 4, 5),
    )

    items = memory_routes._flatten_memory(memory)

    assert [item.key for item in items] == ["verbosity"]


def test_flatten_memory_coerces_non_string_bucket_keys() -> None:
    memory = SimpleNamespace(
        style_model={2: "verbose", "1": "concise", None: "ignored"},
        work_patterns={},
        project_knowledge={},
        communication_style={},
        structural_preferences={},
        skill_profile={},
        explicit_preferences=[],
        last_updated=None,
    )

    items = memory_routes._flatten_memory(memory)

    assert [item.key for item in items] == ["1", "2"]
    assert [item.value for item in items] == ["concise", "verbose"]


def test_memory_int_coercion_rejects_non_finite_values() -> None:
    assert memory_routes._coerce_memory_int(float("nan"), fallback=-1) == -1
    assert memory_routes._coerce_memory_int(float("inf"), fallback=-1) == -1
    assert memory_routes._coerce_memory_int("-inf", fallback=-1) == -1
    assert memory_routes._coerce_memory_int("3", fallback=-1) == 3


def test_memory_row_list_coercion_rejects_malformed_results() -> None:
    row = SimpleNamespace(id="row-1")

    assert memory_routes._coerce_memory_row_list([row]) == [row]
    assert memory_routes._coerce_memory_row_list((row,)) == [row]
    assert memory_routes._coerce_memory_row_list(None) == []
    assert memory_routes._coerce_memory_row_list("bad") == []


def test_stringify_value_sanitizes_non_finite_json() -> None:
    raw = memory_routes._stringify_value({
        "stress": float("inf"),
        "nested": (float("nan"),),
        "set_metric": {float("inf")},
    })

    payload = json.loads(raw)

    assert memory_routes._stringify_value(float("inf")) == "0.0"
    assert payload == {
        "stress": 0.0,
        "nested": [0.0],
        "set_metric": [0.0],
    }
    json.dumps(payload, allow_nan=False)


def test_stringify_value_serializes_nested_non_json_edge_values() -> None:
    class _Opaque:
        def __str__(self) -> str:
            return "opaque-value"

    raw = memory_routes._stringify_value(
        {
            ("tuple", "key"): b"memory-bytes",
            "set_values": {"b", "a"},
            "nested": {"opaque": _Opaque()},
        }
    )

    payload = json.loads(raw)

    assert payload == {
        "('tuple', 'key')": "memory-bytes",
        "set_values": ["a", "b"],
        "nested": {"opaque": "opaque-value"},
    }
    json.dumps(payload, allow_nan=False)


def test_stringify_value_sanitizes_cyclic_json_payloads() -> None:
    cycle: dict[str, object] = {"type": "preference"}
    cycle["self"] = cycle

    raw = memory_routes._stringify_value(cycle)

    assert json.loads(raw) == {
        "type": "preference",
        "self": "[Circular]",
    }


def test_reset_all_memory_route_precedes_item_delete_route() -> None:
    delete_paths = [
        route.path
        for route in memory_routes.router.routes
        if "DELETE" in getattr(route, "methods", set())
    ]

    assert delete_paths.index("/memory/all") < delete_paths.index("/memory/{item_id}")


def test_flatten_memory_stringifies_unserializable_values() -> None:
    class _Unserializable:
        def __str__(self) -> str:
            return "line1\\nline2"

    memory = SimpleNamespace(
        style_model={"verbosity": _Unserializable()},
        work_patterns={},
        project_knowledge={},
        communication_style={},
        structural_preferences={},
        skill_profile={},
        explicit_preferences=[],
        last_updated=None,
    )

    items = memory_routes._flatten_memory(memory)

    assert len(items) == 1
    assert items[0].value == "line1\\nline2"


@pytest.mark.asyncio
async def test_update_memory_item_rejects_unknown_field_ids() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await memory_routes.update_memory_item(
            "unknown:key",
            memory_routes.UpdateMemoryItemRequest(value="updated"),
            current_user=SimpleNamespace(id="user-1"),
            db=None,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid memory item id"


@pytest.mark.asyncio
async def test_get_memory_timeline_preserves_zero_indexes_in_keys() -> None:
    log = SimpleNamespace(
        id="log-1",
        field_updated="explicit_preferences",
        update_type="explicit_delete",
        new_value=None,
        previous_value={"index": 0, "preference": "be concise"},
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )

    timeline = await memory_routes.get_memory_timeline(
        current_user=SimpleNamespace(id="user-1"),
        db=_TimelineDB([log]),
    )

    assert len(timeline) == 1
    assert timeline[0].key == "0"
    assert timeline[0].value == "be concise"
    assert timeline[0].action == "removed"


@pytest.mark.asyncio
async def test_get_memory_timeline_uses_query_default_when_called_directly() -> None:
    log = SimpleNamespace(
        id="log-1",
        field_updated="style_model",
        update_type="manual_update",
        new_value={"key": "verbosity", "value": "concise"},
        previous_value=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )

    timeline = await memory_routes.get_memory_timeline(
        current_user=SimpleNamespace(id="user-1"),
        db=_TimelineDB([log]),
    )

    assert len(timeline) == 1
    assert timeline[0].timestamp == "2024-01-01T12:00:00"


@pytest.mark.asyncio
async def test_get_memory_timeline_tolerates_string_timestamps() -> None:
    log = SimpleNamespace(
        id="log-1",
        field_updated="style_model",
        update_type="manual_update",
        new_value={"key": "verbosity", "value": "concise"},
        previous_value=None,
        created_at=" 2026-01-02T03:04:05Z ",
    )

    timeline = await memory_routes.get_memory_timeline(
        current_user=SimpleNamespace(id="user-1"),
        db=_TimelineDB([log]),
    )

    assert len(timeline) == 1
    assert timeline[0].timestamp == "2026-01-02T03:04:05Z"


@pytest.mark.asyncio
async def test_get_memory_timeline_parses_json_string_payloads() -> None:
    log = SimpleNamespace(
        id="log-1",
        field_updated="style_model",
        update_type="manual_update",
        new_value='{"key": "verbosity", "value": "concise"}',
        previous_value=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )

    timeline = await memory_routes.get_memory_timeline(
        current_user=SimpleNamespace(id="user-1"),
        db=_TimelineDB([log]),
    )

    assert len(timeline) == 1
    assert timeline[0].key == "verbosity"
    assert timeline[0].value == "concise"


@pytest.mark.asyncio
async def test_get_memory_timeline_fails_closed_for_invalid_payloads() -> None:
    log = SimpleNamespace(
        id="log-1",
        field_updated="style_model",
        update_type="manual_update",
        new_value="oops",
        previous_value=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )

    timeline = await memory_routes.get_memory_timeline(
        current_user=SimpleNamespace(id="user-1"),
        db=_TimelineDB([log]),
    )

    assert len(timeline) == 1
    assert timeline[0].key == "style_model"
    assert timeline[0].value == ""


@pytest.mark.asyncio
async def test_get_memory_timeline_tolerates_malformed_log_metadata() -> None:
    log = SimpleNamespace(
        field_updated=["style_model"],
        update_type=None,
        new_value=None,
        previous_value=None,
    )

    timeline = await memory_routes.get_memory_timeline(
        current_user=SimpleNamespace(id="user-1"),
        db=_TimelineDB([log]),
    )

    assert len(timeline) == 1
    assert timeline[0].dimension == "personal"
    assert timeline[0].action == "updated"
    assert timeline[0].key == "unknown"
    assert timeline[0].value == ""
    assert timeline[0].id == ""
    assert timeline[0].timestamp == ""


@pytest.mark.asyncio
async def test_get_memory_timeline_stringifies_unserializable_values() -> None:
    class _Unserializable:
        def __str__(self) -> str:
            return "line1\\nline2"

    log = SimpleNamespace(
        id="log-1",
        field_updated="style_model",
        update_type="manual_update",
        new_value={"key": "verbosity", "value": _Unserializable()},
        previous_value=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )

    timeline = await memory_routes.get_memory_timeline(
        current_user=SimpleNamespace(id="user-1"),
        db=_TimelineDB([log]),
    )

    assert len(timeline) == 1
    assert timeline[0].value == "line1\\nline2"


@pytest.mark.asyncio
async def test_delete_memory_item_rejects_non_numeric_preference_ids() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await memory_routes.delete_memory_item(
            "explicit_preferences:not-a-number",
            current_user=SimpleNamespace(id="user-1"),
            db=None,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Invalid memory item id"


@pytest.mark.asyncio
async def test_delete_memory_item_maps_missing_preference_to_not_found(monkeypatch) -> None:
    db = _FakeDB()

    async def fake_get_or_create_memory(user_id, db_session):
        assert user_id == "user-1"
        assert db_session is db
        return SimpleNamespace(explicit_preferences=[], last_updated=datetime.utcnow(), memory_version=1)

    async def fake_delete_preference(user_id, index, db_session):
        raise IndexError("out of range")

    monkeypatch.setattr(memory_routes, "_get_or_create_memory", fake_get_or_create_memory)
    monkeypatch.setattr(memory_routes.MemoryEngine, "delete_preference", fake_delete_preference)

    with pytest.raises(HTTPException) as exc_info:
        await memory_routes.delete_memory_item(
            "explicit_preferences:4",
            current_user=SimpleNamespace(id="user-1"),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "Memory item not found"


@pytest.mark.asyncio
async def test_delete_memory_item_does_not_mutate_metadata_when_bucket_key_is_missing(
    monkeypatch,
) -> None:
    old_updated_at = datetime(2024, 1, 1, 12, 0, 0)
    memory = SimpleNamespace(
        style_model={"verbosity": "concise"},
        explicit_preferences=[],
        last_updated=old_updated_at,
        memory_version=4,
    )
    db = _FakeDB()

    async def fake_get_or_create_memory(user_id, db_session):
        assert user_id == "user-1"
        assert db_session is db
        return memory

    monkeypatch.setattr(memory_routes, "_get_or_create_memory", fake_get_or_create_memory)

    with pytest.raises(HTTPException) as exc_info:
        await memory_routes.delete_memory_item(
            memory_routes._encode_id("style_model", "missing"),
            current_user=SimpleNamespace(id="user-1"),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "Memory item not found"
    assert memory.memory_version == 4
    assert memory.last_updated == old_updated_at
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_create_memory_item_tolerates_malformed_bucket(monkeypatch) -> None:
    memory = SimpleNamespace(
        style_model="oops",
        explicit_preferences=[],
        last_updated=None,
        memory_version=1,
    )
    db = _FakeDB()

    async def fake_get_or_create_memory(user_id, db_session):
        assert user_id == "user-1"
        assert db_session is db
        return memory

    async def fake_log_memory_update(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(memory_routes, "_get_or_create_memory", fake_get_or_create_memory)
    monkeypatch.setattr(memory_routes, "_log_memory_update", fake_log_memory_update)

    response = await memory_routes.create_memory_item(
        memory_routes.CreateMemoryItemRequest(
            dimension="coding_style",
            key="verbosity",
            value="concise",
        ),
        current_user=SimpleNamespace(id="user-1"),
        db=db,
    )

    assert memory.style_model == {"verbosity": "concise"}
    assert memory.memory_version == 2
    assert memory.last_updated is not None
    assert response.value == "concise"
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_create_memory_item_tolerates_missing_legacy_metadata(
    monkeypatch,
) -> None:
    memory = SimpleNamespace()
    db = _FakeDB()

    async def fake_get_or_create_memory(user_id, db_session):
        assert user_id == "user-1"
        assert db_session is db
        return memory

    async def fake_log_memory_update(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(memory_routes, "_get_or_create_memory", fake_get_or_create_memory)
    monkeypatch.setattr(memory_routes, "_log_memory_update", fake_log_memory_update)

    response = await memory_routes.create_memory_item(
        memory_routes.CreateMemoryItemRequest(
            dimension="coding_style",
            key="verbosity",
            value="concise",
        ),
        current_user=SimpleNamespace(id="user-1"),
        db=db,
    )

    assert memory.style_model == {"verbosity": "concise"}
    assert memory.memory_version == 1
    assert memory.last_updated is not None
    assert response.updated_at == memory.last_updated.isoformat()
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_create_memory_item_coerces_string_memory_version(monkeypatch) -> None:
    memory = SimpleNamespace(
        style_model={},
        explicit_preferences=[],
        last_updated=None,
        memory_version="1",
    )
    db = _FakeDB()

    async def fake_get_or_create_memory(user_id, db_session):
        assert user_id == "user-1"
        assert db_session is db
        return memory

    async def fake_log_memory_update(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(memory_routes, "_get_or_create_memory", fake_get_or_create_memory)
    monkeypatch.setattr(memory_routes, "_log_memory_update", fake_log_memory_update)

    response = await memory_routes.create_memory_item(
        memory_routes.CreateMemoryItemRequest(
            dimension="coding_style",
            key="verbosity",
            value="concise",
        ),
        current_user=SimpleNamespace(id="user-1"),
        db=db,
    )

    assert memory.style_model == {"verbosity": "concise"}
    assert memory.memory_version == 2
    assert response.value == "concise"
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_update_memory_item_updates_metadata_for_bucket_entries(monkeypatch) -> None:
    old_updated_at = datetime(2024, 1, 1, 12, 0, 0)
    memory = SimpleNamespace(
        style_model={"verbosity": "concise"},
        explicit_preferences=[],
        last_updated=old_updated_at,
        memory_version=4,
    )
    db = _FakeDB()

    async def fake_get_or_create_memory(user_id, db_session):
        assert user_id == "user-1"
        assert db_session is db
        return memory

    async def fake_log_memory_update(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(memory_routes, "_get_or_create_memory", fake_get_or_create_memory)
    monkeypatch.setattr(memory_routes, "_log_memory_update", fake_log_memory_update)

    response = await memory_routes.update_memory_item(
        memory_routes._encode_id("style_model", "verbosity"),
        memory_routes.UpdateMemoryItemRequest(value="detailed"),
        current_user=SimpleNamespace(id="user-1"),
        db=db,
    )

    assert memory.style_model["verbosity"] == "detailed"
    assert memory.memory_version == 5
    assert memory.last_updated > old_updated_at
    assert response.updated_at == memory.last_updated.isoformat()
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_update_memory_item_treats_malformed_bucket_as_missing(monkeypatch) -> None:
    old_updated_at = datetime(2024, 1, 1, 12, 0, 0)
    memory = SimpleNamespace(
        style_model="oops",
        explicit_preferences=[],
        last_updated=old_updated_at,
        memory_version=4,
    )
    db = _FakeDB()

    async def fake_get_or_create_memory(user_id, db_session):
        assert user_id == "user-1"
        assert db_session is db
        return memory

    monkeypatch.setattr(memory_routes, "_get_or_create_memory", fake_get_or_create_memory)

    with pytest.raises(HTTPException) as exc_info:
        await memory_routes.update_memory_item(
            memory_routes._encode_id("style_model", "verbosity"),
            memory_routes.UpdateMemoryItemRequest(value="detailed"),
            current_user=SimpleNamespace(id="user-1"),
            db=db,
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "Memory item not found"
    assert memory.memory_version == 4
    assert memory.last_updated == old_updated_at
    assert db.flush_calls == 0
