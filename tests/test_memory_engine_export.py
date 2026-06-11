from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from codey.saas.memory.engine import MemoryEngine


class _MemoryDB:
    def __init__(self, memory) -> None:
        self._memory = memory

    async def get(self, model, user_id):
        return self._memory


@pytest.mark.asyncio
async def test_export_memory_tolerates_string_last_updated() -> None:
    user_id = uuid.uuid4()
    memory = SimpleNamespace(
        user_id=user_id,
        style_model={"verbosity": "concise"},
        work_patterns={},
        project_knowledge={},
        communication_style={},
        structural_preferences={},
        skill_profile={},
        explicit_preferences=[],
        proactive_queue=[],
        memory_version=3,
        last_updated=" 2026-01-02T03:04:05Z ",
        total_sessions_analyzed=7,
    )

    payload = await MemoryEngine.export_memory(user_id, _MemoryDB(memory))

    assert payload["last_updated"] == "2026-01-02T03:04:05Z"
    assert payload["memory_version"] == 3


@pytest.mark.asyncio
async def test_export_memory_coerces_legacy_numeric_strings() -> None:
    user_id = uuid.uuid4()
    memory = SimpleNamespace(
        user_id=user_id,
        style_model={},
        work_patterns={},
        project_knowledge={},
        communication_style={},
        structural_preferences={},
        skill_profile={},
        explicit_preferences=[],
        proactive_queue=[],
        memory_version="4",
        last_updated=None,
        total_sessions_analyzed="7",
    )

    payload = await MemoryEngine.export_memory(user_id, _MemoryDB(memory))

    assert payload["memory_version"] == 4
    assert payload["total_sessions_analyzed"] == 7


@pytest.mark.asyncio
async def test_export_memory_tolerates_missing_legacy_fields() -> None:
    user_id = uuid.uuid4()
    memory = SimpleNamespace(user_id=user_id)

    payload = await MemoryEngine.export_memory(user_id, _MemoryDB(memory))

    assert payload == {
        "user_id": str(user_id),
        "style_model": {},
        "work_patterns": {},
        "project_knowledge": {},
        "communication_style": {},
        "structural_preferences": {},
        "skill_profile": {},
        "explicit_preferences": [],
        "proactive_queue": [],
        "memory_version": 0,
        "last_updated": None,
        "total_sessions_analyzed": 0,
    }
