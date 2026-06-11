from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from codey.saas.memory.engine import (
    MemoryEngine,
    _coerce_memory_float,
    _coerce_memory_int,
    _coerce_memory_row_list,
)
from codey.saas.models.coding_session import CodingSession
from codey.saas.models.user_memory import UserMemory


class _MemoryEngineDB:
    def __init__(self, session, memory, rows=None) -> None:
        self._session = session
        self._memory = memory
        self._rows = list(rows or [])
        self.added: list[object] = []

    async def get(self, model, object_id):
        if model is CodingSession:
            return self._session
        if model is UserMemory:
            return self._memory
        raise AssertionError(f"Unexpected model lookup: {model!r} {object_id!r}")

    def add(self, value) -> None:
        self.added.append(value)

    async def execute(self, _statement):
        rows = self._rows

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return rows

        return _Result()

    async def flush(self) -> None:
        return None


def test_memory_numeric_coercion_rejects_non_finite_values() -> None:
    assert _coerce_memory_int(float("nan"), 7) == 7
    assert _coerce_memory_int(float("inf"), 7) == 7
    assert _coerce_memory_int("1e309", 7) == 7
    assert _coerce_memory_float("nan", 0.5) == 0.5
    assert _coerce_memory_float("inf", 0.5) == 0.5
    assert _coerce_memory_float(10**10000, 0.5) == 0.5
    assert _coerce_memory_float("42.5", 0.5) == 42.5


def test_memory_row_list_coercion_rejects_malformed_results() -> None:
    row = SimpleNamespace(id="row-1")

    assert _coerce_memory_row_list([row]) == [row]
    assert _coerce_memory_row_list((row,)) == [row]
    assert _coerce_memory_row_list(None) == []
    assert _coerce_memory_row_list("bad") == []


@pytest.mark.asyncio
async def test_run_memory_extraction_tolerates_legacy_memory_shapes() -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = SimpleNamespace(
        prompt="brief. always use tests.",
        output_summary="walk me through next steps",
        mode="prompt",
        lines_generated=12,
        started_at=datetime(2026, 1, 2, 9, 0, 0),
        completed_at=datetime(2026, 1, 2, 9, 30, 0),
    )
    memory = SimpleNamespace(
        user_id=user_id,
        style_model=None,
        work_patterns="not-json",
        project_knowledge=[],
        communication_style="oops",
        structural_preferences=0,
        skill_profile=None,
        explicit_preferences=None,
        proactive_queue=None,
        memory_version=1,
        last_updated=None,
        total_sessions_analyzed=0,
    )

    updated = await MemoryEngine.run_memory_extraction(
        session_id,
        user_id,
        _MemoryEngineDB(session, memory),
    )

    assert updated is memory
    assert memory.style_model["concise"] > 0
    assert memory.communication_style["prefers_explanations"] is True
    assert memory.skill_profile["mode_usage"]["prompt"] == 1
    assert memory.skill_profile["total_lines_generated"] == 12
    assert memory.work_patterns["time_distribution"]["morning"] == 1
    assert memory.work_patterns["avg_session_minutes"] == 30.0
    assert "use tests" in memory.explicit_preferences
    assert memory.total_sessions_analyzed == 1
    assert memory.memory_version == 2


@pytest.mark.asyncio
async def test_run_memory_extraction_tolerates_missing_legacy_memory_fields() -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = SimpleNamespace(
        prompt="brief. always use tests.",
        output_summary="walk me through next steps",
        mode="prompt",
        lines_generated=12,
        started_at=datetime(2026, 1, 2, 9, 0, 0),
        completed_at=datetime(2026, 1, 2, 9, 30, 0),
    )
    memory = SimpleNamespace(user_id=user_id)

    updated = await MemoryEngine.run_memory_extraction(
        session_id,
        user_id,
        _MemoryEngineDB(session, memory),
    )

    assert updated is memory
    assert memory.style_model["concise"] > 0
    assert memory.communication_style["prefers_explanations"] is True
    assert memory.explicit_preferences == ["use tests"]
    assert memory.total_sessions_analyzed == 1
    assert memory.memory_version == 1


@pytest.mark.asyncio
async def test_run_memory_extraction_tolerates_missing_session_fields() -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = SimpleNamespace(prompt="brief. always use tests.")
    memory = SimpleNamespace(user_id=user_id)

    updated = await MemoryEngine.run_memory_extraction(
        session_id,
        user_id,
        _MemoryEngineDB(session, memory),
    )

    assert updated is memory
    assert memory.style_model["concise"] > 0
    assert memory.explicit_preferences == ["use tests"]
    assert memory.skill_profile == {}
    assert memory.work_patterns == {}
    assert memory.total_sessions_analyzed == 1
    assert memory.memory_version == 1


@pytest.mark.asyncio
async def test_build_memory_context_tolerates_malformed_memory_fields() -> None:
    user_id = uuid.uuid4()
    memory = SimpleNamespace(
        user_id=user_id,
        style_model={"concise": 0.8, "verbose": "high"},
        work_patterns={"time_distribution": ["night"], "avg_session_minutes": "long"},
        project_knowledge="oops",
        communication_style="oops",
        structural_preferences=[],
        skill_profile={"mode_usage": ["prompt"]},
        explicit_preferences="oops",
        total_sessions_analyzed=4,
    )

    context = await MemoryEngine.build_memory_context(
        user_id,
        _MemoryEngineDB(None, memory),
    )

    assert "## WHAT YOU KNOW ABOUT THIS USER" in context
    assert "concise (80%)" in context
    assert "_Based on 4 sessions analyzed._" in context


@pytest.mark.asyncio
async def test_build_memory_context_tolerates_missing_legacy_fields() -> None:
    user_id = uuid.uuid4()

    context = await MemoryEngine.build_memory_context(
        user_id,
        _MemoryEngineDB(None, SimpleNamespace(user_id=user_id)),
    )

    assert context == "## WHAT YOU KNOW ABOUT THIS USER\n"


@pytest.mark.asyncio
async def test_build_memory_context_coerces_legacy_summary_counters() -> None:
    user_id = uuid.uuid4()
    memory = SimpleNamespace(
        user_id=user_id,
        style_model={},
        work_patterns={
            "time_distribution": {"night": "7", "morning": 2},
            "avg_session_minutes": "45",
        },
        project_knowledge={},
        communication_style={},
        structural_preferences={},
        skill_profile={"mode_usage": {"chat": "10", "builder": "3", "prompt": 2}},
        explicit_preferences=[],
        total_sessions_analyzed="6",
    )

    context = await MemoryEngine.build_memory_context(
        user_id,
        _MemoryEngineDB(None, memory),
    )

    assert "**Peak working time:** night" in context
    assert "**Avg session length:** 45 min" in context
    assert "**Favorite modes:** chat: 10, builder: 3, prompt: 2" in context
    assert "_Based on 6 sessions analyzed._" in context


@pytest.mark.asyncio
async def test_run_memory_extraction_coerces_legacy_numeric_strings() -> None:
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = SimpleNamespace(
        prompt="brief. always use tests.",
        output_summary="walk me through next steps",
        mode="prompt",
        lines_generated=12,
        started_at=datetime(2026, 1, 2, 9, 0, 0),
        completed_at=datetime(2026, 1, 2, 9, 30, 0),
    )
    memory = SimpleNamespace(
        user_id=user_id,
        style_model=None,
        work_patterns={"time_distribution": {"morning": "3"}, "avg_session_minutes": "45"},
        project_knowledge=[],
        communication_style="oops",
        structural_preferences=0,
        skill_profile={"mode_usage": {"prompt": "2"}, "total_lines_generated": "10"},
        explicit_preferences=None,
        proactive_queue=None,
        memory_version="4",
        last_updated=None,
        total_sessions_analyzed="2",
    )

    updated = await MemoryEngine.run_memory_extraction(
        session_id,
        user_id,
        _MemoryEngineDB(session, memory),
    )

    assert updated is memory
    assert memory.skill_profile["mode_usage"]["prompt"] == 3
    assert memory.skill_profile["total_lines_generated"] == 22
    assert memory.work_patterns["time_distribution"]["morning"] == 4
    assert memory.work_patterns["avg_session_minutes"] == 40.0
    assert memory.total_sessions_analyzed == 3
    assert memory.memory_version == 5


@pytest.mark.asyncio
async def test_add_explicit_preference_coerces_string_memory_version() -> None:
    user_id = uuid.uuid4()
    memory = SimpleNamespace(
        user_id=user_id,
        explicit_preferences=[],
        memory_version="4",
        last_updated=None,
    )
    db = _MemoryEngineDB(None, memory)

    updated = await MemoryEngine.add_explicit_preference(user_id, "use tests", db)

    assert updated is memory
    assert memory.explicit_preferences == ["use tests"]
    assert memory.memory_version == 5
    assert memory.last_updated is not None


@pytest.mark.asyncio
async def test_add_explicit_preference_tolerates_missing_legacy_metadata() -> None:
    user_id = uuid.uuid4()
    memory = SimpleNamespace(user_id=user_id)
    db = _MemoryEngineDB(None, memory)

    updated = await MemoryEngine.add_explicit_preference(user_id, "use tests", db)

    assert updated is memory
    assert memory.explicit_preferences == ["use tests"]
    assert memory.memory_version == 1
    assert memory.last_updated is not None


@pytest.mark.asyncio
async def test_delete_preference_coerces_string_memory_version() -> None:
    user_id = uuid.uuid4()
    memory = SimpleNamespace(
        user_id=user_id,
        explicit_preferences=["use tests", "be concise"],
        memory_version="4",
        last_updated=None,
    )
    db = _MemoryEngineDB(None, memory)

    updated = await MemoryEngine.delete_preference(user_id, 0, db)

    assert updated is memory
    assert memory.explicit_preferences == ["be concise"]
    assert memory.memory_version == 5
    assert memory.last_updated is not None


@pytest.mark.asyncio
async def test_reset_memory_coerces_string_memory_version() -> None:
    user_id = uuid.uuid4()
    memory = SimpleNamespace(
        user_id=user_id,
        style_model={"concise": 1.0},
        work_patterns={"time_distribution": {"morning": 2}},
        project_knowledge={"languages": ["python"]},
        communication_style={"prefers_explanations": True},
        structural_preferences={"quotes": "single"},
        skill_profile={"mode_usage": {"prompt": 2}},
        explicit_preferences=["use tests"],
        proactive_queue=["ship a helper"],
        memory_version="4",
        total_sessions_analyzed=3,
        last_updated=None,
    )
    db = _MemoryEngineDB(None, memory)

    updated = await MemoryEngine.reset_memory(user_id, db)

    assert updated is memory
    assert memory.style_model == {}
    assert memory.work_patterns == {}
    assert memory.project_knowledge == {}
    assert memory.communication_style == {}
    assert memory.structural_preferences == {}
    assert memory.skill_profile == {}
    assert memory.explicit_preferences == []
    assert memory.proactive_queue == []
    assert memory.memory_version == 5
    assert memory.total_sessions_analyzed == 0
    assert memory.last_updated is not None


@pytest.mark.asyncio
async def test_run_proactive_analysis_coerces_legacy_numeric_strings() -> None:
    user_id = uuid.uuid4()
    memory = SimpleNamespace(
        user_id=user_id,
        work_patterns={"time_distribution": {"night": "3"}, "avg_session_minutes": "150"},
        total_sessions_analyzed="6",
    )
    db = _MemoryEngineDB(None, memory, rows=[])

    insights = await MemoryEngine.run_proactive_analysis(user_id, db)

    assert [item["type"] for item in insights] == ["stress_trend", "session_length"]
    assert insights[0]["data"]["night_ratio"] == 0.5
    assert insights[0]["data"]["total_sessions"] == 6
    assert insights[1]["data"]["avg_minutes"] == 150.0


@pytest.mark.asyncio
async def test_run_proactive_analysis_tolerates_missing_legacy_fields() -> None:
    user_id = uuid.uuid4()

    insights = await MemoryEngine.run_proactive_analysis(
        user_id,
        _MemoryEngineDB(None, SimpleNamespace(user_id=user_id), rows=[]),
    )

    assert insights == []
