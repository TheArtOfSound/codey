from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

import codey.saas.agents.orchestrator as orchestrator_module
from codey.saas.agents.orchestrator import (
    Agent,
    AgentOrchestrator,
    AgentResult,
    AgentRole,
    AgentStatus,
    SubTask,
)
from codey.saas.sandbox.manager import Sandbox


def test_parse_subtasks_falls_back_when_json_is_not_a_list() -> None:
    orchestrator = AgentOrchestrator(sandbox_manager=object())

    subtasks = orchestrator._parse_subtasks(
        '{"id":"st-1","description":"Single object instead of array"}'
    )

    assert len(subtasks) == 1
    assert subtasks[0].id == "st-1"
    assert subtasks[0].role is AgentRole.BUILDER
    assert subtasks[0].description == '{"id":"st-1","description":"Single object instead of array"}'


def test_parse_subtasks_skips_invalid_entries_and_normalizes_fields() -> None:
    orchestrator = AgentOrchestrator(sandbox_manager=object())

    subtasks = orchestrator._parse_subtasks(
        json.dumps(
            [
                "invalid-entry",
                {
                    "id": 123,
                    "description": 99,
                    "role": "reviewer",
                    "files": "not-a-list",
                    "dependencies": [None, " st-0 ", 42],
                },
                {
                    "id": "  ",
                    "description": "Build feature",
                    "role": "builder",
                    "files": [
                        " app.py ",
                        ".\\src\\ui.tsx",
                        "../secret.py",
                        "bad\nname.py",
                        7,
                        "   ",
                    ],
                    "dependencies": "st-1",
                },
            ]
        )
    )

    assert len(subtasks) == 2

    assert subtasks[0].id == "st-1"
    assert subtasks[0].description == "99"
    assert subtasks[0].role is AgentRole.REVIEWER
    assert subtasks[0].files == []
    assert subtasks[0].dependencies == ["st-0"]
    assert subtasks[0].priority == 2

    assert subtasks[1].id == "st-2"
    assert subtasks[1].description == "Build feature"
    assert subtasks[1].role is AgentRole.BUILDER
    assert subtasks[1].files == ["app.py", "src/ui.tsx"]
    assert subtasks[1].dependencies == []
    assert subtasks[1].priority == 1


@pytest.mark.asyncio
async def test_decompose_task_falls_back_when_model_returns_non_string(monkeypatch) -> None:
    orchestrator = AgentOrchestrator(sandbox_manager=object())

    async def fake_call_model(*args, **kwargs):
        return {"plan": "Investigate task"}

    monkeypatch.setattr(
        orchestrator_module,
        "resolve_model",
        lambda key: ("provider", "model"),
    )
    monkeypatch.setattr(orchestrator_module, "call_model", fake_call_model)

    subtasks = await orchestrator.decompose_task("Investigate task")

    assert len(subtasks) == 1
    assert subtasks[0].role is AgentRole.BUILDER
    assert "Investigate task" in subtasks[0].description


@pytest.mark.asyncio
async def test_execute_agent_stringifies_non_string_model_output(monkeypatch, tmp_path: Path) -> None:
    class DummySandboxManager:
        async def read_file(self, sandbox_id: str, path: str) -> str:
            raise FileNotFoundError(path)

        async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
            raise AssertionError("No files should be written for non-code output")

    orchestrator = AgentOrchestrator(sandbox_manager=DummySandboxManager())
    agent = Agent(
        id="https://agent-user:secret@example.com/agent?token=agent-token",
        role=AgentRole.BUILDER,
        subtask=SubTask(
            id="st-1",
            description="Generate output",
            role=AgentRole.BUILDER,
            files=["app.py"],
        ),
    )
    sandbox = Sandbox(
        id="sbx-1",
        user_id="user-1",
        session_id="session-1",
        root=tmp_path,
    )

    async def fake_call_model(*args, **kwargs):
        return {"content": "structured output"}

    monkeypatch.setattr(
        orchestrator_module,
        "resolve_model",
        lambda key: ("provider", "model"),
    )
    monkeypatch.setattr(orchestrator_module, "call_model", fake_call_model)

    result = await orchestrator._execute_agent(agent, sandbox, prior_results={})

    assert result.success is True
    assert result.files_modified == []
    assert "structured output" in result.output
    assert agent.output == result.output


@pytest.mark.asyncio
async def test_execute_agent_rejects_empty_model_output(monkeypatch, tmp_path: Path) -> None:
    class DummySandboxManager:
        async def read_file(self, sandbox_id: str, path: str) -> str:
            raise FileNotFoundError(path)

        async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
            raise AssertionError("No files should be written for empty output")

    orchestrator = AgentOrchestrator(sandbox_manager=DummySandboxManager())
    agent = Agent(
        id="agent-1",
        role=AgentRole.BUILDER,
        subtask=SubTask(
            id="st-1",
            description="Generate output",
            role=AgentRole.BUILDER,
            files=["app.py"],
        ),
    )
    sandbox = Sandbox(
        id="sbx-1",
        user_id="user-1",
        session_id="session-1",
        root=tmp_path,
    )

    async def fake_call_model(*args, **kwargs):
        return " \n\t"

    monkeypatch.setattr(
        orchestrator_module,
        "resolve_model",
        lambda key: ("provider", "model"),
    )
    monkeypatch.setattr(orchestrator_module, "call_model", fake_call_model)

    result = await orchestrator._execute_agent(agent, sandbox, prior_results={})

    assert result.success is False
    assert result.output == ""
    assert result.error == "Model returned empty agent output"
    assert agent.status is AgentStatus.FAILED
    assert agent.error == "Model returned empty agent output"


@pytest.mark.asyncio
async def test_execute_agent_redacts_credentials_from_failures(
    caplog,
    monkeypatch,
    tmp_path: Path,
) -> None:
    class DummySandboxManager:
        async def read_file(self, sandbox_id: str, path: str) -> str:
            raise FileNotFoundError(path)

    orchestrator = AgentOrchestrator(sandbox_manager=DummySandboxManager())
    agent = Agent(
        id="agent-1",
        role=AgentRole.BUILDER,
        subtask=SubTask(
            id="st-1",
            description="Generate output",
            role=AgentRole.BUILDER,
            files=["app.py"],
        ),
    )
    sandbox = Sandbox(
        id="sbx-1",
        user_id="user-1",
        session_id="session-1",
        root=tmp_path,
    )

    async def fake_call_model(*args, **kwargs):
        raise RuntimeError(
            "provider failed "
            "https://user:url-secret@example.com/owner/repo.git"
            "?token=repo-token&client_secret=query-client-secret "
            "access_token=access-secret auth_token=auth-secret "
            "refresh_token=refresh-secret password=inline-password "
            "for user@example.com authorization=Bearer bearer-secret"
        )

    monkeypatch.setattr(
        orchestrator_module,
        "resolve_model",
        lambda key: ("provider", "model"),
    )
    monkeypatch.setattr(orchestrator_module, "call_model", fake_call_model)
    caplog.set_level(logging.WARNING, logger="codey.saas.agents.orchestrator")

    result = await orchestrator._execute_agent(agent, sandbox, prior_results={})

    assert result.success is False
    assert agent.error == result.error
    assert "user@example.com" not in result.error
    assert "url-secret" not in result.error
    assert "repo-token" not in result.error
    assert "query-client-secret" not in result.error
    assert "access-secret" not in result.error
    assert "auth-secret" not in result.error
    assert "refresh-secret" not in result.error
    assert "inline-password" not in result.error
    assert "bearer-secret" not in result.error
    assert "***@example.com" in result.error
    assert "https://***@example.com/owner/repo.git?token=***&client_secret=***" in result.error
    assert "access_token=***" in result.error
    assert "auth_token=***" in result.error
    assert "refresh_token=***" in result.error
    assert "password=***" in result.error
    assert "authorization=Bearer ***" in result.error
    assert "user@example.com" not in caplog.text
    assert "url-secret" not in caplog.text
    assert "agent-token" not in caplog.text
    assert "repo-token" not in caplog.text
    assert "query-client-secret" not in caplog.text
    assert "access-secret" not in caplog.text
    assert "auth-secret" not in caplog.text
    assert "refresh-secret" not in caplog.text
    assert "inline-password" not in caplog.text
    assert "bearer-secret" not in caplog.text
    assert "***@example.com" in caplog.text
    assert "https://***@example.com/agent?token=***" in caplog.text
    assert "https://***@example.com/owner/repo.git?token=***&client_secret=***" in caplog.text
    assert "access_token=***" in caplog.text
    assert "auth_token=***" in caplog.text
    assert "refresh_token=***" in caplog.text
    assert "password=***" in caplog.text
    assert "authorization=Bearer ***" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_run_parallel_clamps_non_positive_max_parallel(
    monkeypatch,
    tmp_path: Path,
) -> None:
    orchestrator = AgentOrchestrator(sandbox_manager=object())
    agent = Agent(
        id="agent-1",
        role=AgentRole.BUILDER,
        subtask=SubTask(
            id="st-1",
            description="Generate output",
            role=AgentRole.BUILDER,
        ),
    )
    sandbox = Sandbox(
        id="sbx-1",
        user_id="user-1",
        session_id="session-1",
        root=tmp_path,
    )

    async def fake_execute_agent(
        agent_arg: Agent,
        _sandbox: Sandbox,
        _prior_results: dict[str, AgentResult],
    ) -> AgentResult:
        return AgentResult(
            agent_id=agent_arg.id,
            role=agent_arg.role,
            success=True,
            output="ok",
        )

    monkeypatch.setattr(orchestrator, "_execute_agent", fake_execute_agent)

    results = await asyncio.wait_for(
        orchestrator.run_parallel([agent], sandbox, max_parallel=0),
        timeout=1,
    )

    assert len(results) == 1
    assert results[0].success is True


@pytest.mark.asyncio
async def test_run_parallel_skips_dependents_when_dependency_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    orchestrator = AgentOrchestrator(sandbox_manager=object())
    prerequisite = Agent(
        id="agent-1",
        role=AgentRole.BUILDER,
        subtask=SubTask(
            id="st-1",
            description="Generate prerequisite",
            role=AgentRole.BUILDER,
        ),
    )
    dependent = Agent(
        id="agent-2",
        role=AgentRole.TESTER,
        subtask=SubTask(
            id="st-2",
            description="Test generated prerequisite",
            role=AgentRole.TESTER,
            dependencies=["st-1"],
        ),
    )
    sandbox = Sandbox(
        id="sbx-1",
        user_id="user-1",
        session_id="session-1",
        root=tmp_path,
    )
    calls: list[str] = []

    async def fake_execute_agent(
        agent_arg: Agent,
        _sandbox: Sandbox,
        _prior_results: dict[str, AgentResult],
    ) -> AgentResult:
        calls.append(agent_arg.subtask.id)
        return AgentResult(
            agent_id=agent_arg.id,
            role=agent_arg.role,
            success=False,
            output="",
            error="provider failed",
        )

    monkeypatch.setattr(orchestrator, "_execute_agent", fake_execute_agent)

    results = await orchestrator.run_parallel(
        [prerequisite, dependent],
        sandbox,
        max_parallel=2,
    )

    assert calls == ["st-1"]
    assert len(results) == 2
    assert results[0].agent_id == "agent-1"
    assert results[0].success is False
    assert results[1].agent_id == "agent-2"
    assert results[1].success is False
    assert results[1].error == "Skipped because dependencies failed: st-1"
    assert dependent.status is AgentStatus.FAILED
    assert dependent.error == results[1].error


@pytest.mark.asyncio
async def test_run_parallel_skips_agents_with_missing_dependencies(
    monkeypatch,
    tmp_path: Path,
) -> None:
    orchestrator = AgentOrchestrator(sandbox_manager=object())
    dependent = Agent(
        id="agent-1",
        role=AgentRole.TESTER,
        subtask=SubTask(
            id="st-1",
            description="Test missing prerequisite",
            role=AgentRole.TESTER,
            dependencies=["st-missing"],
        ),
    )
    sandbox = Sandbox(
        id="sbx-1",
        user_id="user-1",
        session_id="session-1",
        root=tmp_path,
    )

    async def fake_execute_agent(
        _agent: Agent,
        _sandbox: Sandbox,
        _prior_results: dict[str, AgentResult],
    ) -> AgentResult:
        raise AssertionError("agent with missing dependency should not run")

    monkeypatch.setattr(orchestrator, "_execute_agent", fake_execute_agent)

    results = await orchestrator.run_parallel(
        [dependent],
        sandbox,
        max_parallel=2,
    )

    assert len(results) == 1
    assert results[0].agent_id == "agent-1"
    assert results[0].success is False
    assert results[0].error == "Skipped because dependencies are missing: st-missing"
    assert dependent.status is AgentStatus.FAILED
    assert dependent.error == results[0].error


@pytest.mark.asyncio
async def test_run_parallel_skips_dependency_deadlocks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    orchestrator = AgentOrchestrator(sandbox_manager=object())
    first = Agent(
        id="agent-1",
        role=AgentRole.BUILDER,
        subtask=SubTask(
            id="st-1",
            description="Build first",
            role=AgentRole.BUILDER,
            dependencies=["st-2"],
        ),
    )
    second = Agent(
        id="agent-2",
        role=AgentRole.TESTER,
        subtask=SubTask(
            id="st-2",
            description="Test second",
            role=AgentRole.TESTER,
            dependencies=["st-1"],
        ),
    )
    sandbox = Sandbox(
        id="sbx-1",
        user_id="user-1",
        session_id="session-1",
        root=tmp_path,
    )

    async def fake_execute_agent(
        _agent: Agent,
        _sandbox: Sandbox,
        _prior_results: dict[str, AgentResult],
    ) -> AgentResult:
        raise AssertionError("deadlocked agents should not run")

    monkeypatch.setattr(orchestrator, "_execute_agent", fake_execute_agent)

    results = await orchestrator.run_parallel(
        [first, second],
        sandbox,
        max_parallel=2,
    )

    assert [result.agent_id for result in results] == ["agent-1", "agent-2"]
    assert [result.success for result in results] == [False, False]
    assert results[0].error == (
        "Skipped because dependency deadlock left unresolved dependencies: st-2"
    )
    assert results[1].error == (
        "Skipped because dependency deadlock left unresolved dependencies: st-1"
    )
    assert first.status is AgentStatus.FAILED
    assert second.status is AgentStatus.FAILED


@pytest.mark.asyncio
async def test_write_output_files_normalizes_extracted_file_paths(tmp_path: Path) -> None:
    class DummySandboxManager:
        def __init__(self) -> None:
            self.writes: list[tuple[str, str, str]] = []

        async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
            self.writes.append((sandbox_id, path, content))

    sandbox_manager = DummySandboxManager()
    orchestrator = AgentOrchestrator(sandbox_manager=sandbox_manager)
    sandbox = Sandbox(
        id="sbx-1",
        user_id="user-1",
        session_id="session-1",
        root=tmp_path,
    )
    output = (
        "### ../secret.py\n```python\nprint('bad')\n```\n"
        "### .\\src\\app.py\n```python\nprint('ok')\n```\n"
    )

    written = await orchestrator._write_output_files(sandbox, output, expected_files=[])

    assert written == ["src/app.py"]
    assert sandbox_manager.writes == [
        ("sbx-1", "src/app.py", "print('ok')\n")
    ]


@pytest.mark.asyncio
async def test_write_output_files_uses_first_safe_expected_file(tmp_path: Path) -> None:
    class DummySandboxManager:
        def __init__(self) -> None:
            self.writes: list[tuple[str, str, str]] = []

        async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
            self.writes.append((sandbox_id, path, content))

    sandbox_manager = DummySandboxManager()
    orchestrator = AgentOrchestrator(sandbox_manager=sandbox_manager)
    sandbox = Sandbox(
        id="sbx-1",
        user_id="user-1",
        session_id="session-1",
        root=tmp_path,
    )

    written = await orchestrator._write_output_files(
        sandbox,
        "```python\nprint('ok')\n```",
        expected_files=["../secret.py", ".\\src\\app.py"],
    )

    assert written == ["src/app.py"]
    assert sandbox_manager.writes == [
        ("sbx-1", "src/app.py", "print('ok')\n")
    ]
