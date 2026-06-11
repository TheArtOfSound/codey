from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, status
from pydantic import ValidationError

import codey.saas.api.repo_routes as repo_routes


def test_repo_nfet_candidates_request_rejects_non_positive_limit() -> None:
    with pytest.raises(ValidationError):
        repo_routes.RepoNFETCandidatesRequest(limit=0)


def test_repo_nfet_candidates_request_normalizes_blank_optional_fields() -> None:
    request = repo_routes.RepoNFETCandidatesRequest(
        goal="  reduce coupling  ",
        target_file="   ",
    )

    assert request.goal == "reduce coupling"
    assert request.target_file is None


def test_repo_nfet_simulation_request_requires_candidate_selector() -> None:
    with pytest.raises(ValidationError):
        repo_routes.RepoNFETSimulationRequest()


def test_repo_nfet_simulation_request_normalizes_blank_optional_fields() -> None:
    request = repo_routes.RepoNFETSimulationRequest(
        candidate_id="  candidate-1  ",
        target_component="   ",
        goal="  reduce coupling  ",
    )

    assert request.candidate_id == "candidate-1"
    assert request.target_component is None
    assert request.goal == "reduce coupling"


@pytest.mark.asyncio
async def test_analyze_repo_nfet_preserves_http_exceptions(monkeypatch) -> None:
    async def fake_build_graph_from_clone_url(*args, **kwargs):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GitHub denied repository access. Reconnect GitHub and try again.",
        )

    monkeypatch.setattr(
        repo_routes,
        "build_graph_from_clone_url",
        fake_build_graph_from_clone_url,
    )

    repo = SimpleNamespace(
        clone_url="https://github.com/owner/repo.git",
        full_name="owner/repo",
    )

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._analyze_repo_nfet(repo, token="gh-token")

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == (
        "GitHub denied repository access. Reconnect GitHub and try again."
    )


@pytest.mark.asyncio
async def test_analyze_repo_nfet_redacts_credentials_from_clone_failures(
    monkeypatch,
) -> None:
    async def fake_build_graph_from_clone_url(*args, **kwargs):
        raise RuntimeError(
            "git clone failed: https://x-access-token:secret@github.com/owner/repo.git"
        )

    monkeypatch.setattr(
        repo_routes,
        "build_graph_from_clone_url",
        fake_build_graph_from_clone_url,
    )

    repo = SimpleNamespace(
        clone_url="https://github.com/owner/repo.git",
        full_name="owner/repo",
    )

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._analyze_repo_nfet(repo, token="gh-token")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert "secret" not in exc_info.value.detail
    assert "https://***@github.com/owner/repo.git" in exc_info.value.detail


@pytest.mark.asyncio
async def test_analyze_repo_nfet_normalizes_blank_goal_and_target_file(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_build_graph_from_clone_url(*args, **kwargs):
        return "graph"

    class _FakeController:
        def analyze(self, graph, goal=None, target_file=None):
            calls["graph"] = graph
            calls["goal"] = goal
            calls["target_file"] = target_file
            return "repo-state"

    monkeypatch.setattr(
        repo_routes,
        "build_graph_from_clone_url",
        fake_build_graph_from_clone_url,
    )
    monkeypatch.setattr(repo_routes, "NFETController", _FakeController)

    repo = SimpleNamespace(
        clone_url="https://github.com/owner/repo.git",
        full_name="owner/repo",
    )

    graph, controller, repo_state = await repo_routes._analyze_repo_nfet(
        repo,
        token="gh-token",
        goal="   ",
        target_file="  ",
    )

    assert graph == "graph"
    assert repo_state == "repo-state"
    assert calls == {
        "graph": "graph",
        "goal": None,
        "target_file": None,
    }


@pytest.mark.asyncio
async def test_analyze_repo_nfet_treats_non_string_clone_url_as_missing(monkeypatch) -> None:
    called = {"value": False}

    async def fake_build_graph_from_clone_url(*args, **kwargs):
        called["value"] = True
        return "graph"

    monkeypatch.setattr(
        repo_routes,
        "build_graph_from_clone_url",
        fake_build_graph_from_clone_url,
    )

    repo = SimpleNamespace(
        clone_url=["https://github.com/owner/repo.git"],
        full_name="owner/repo",
    )

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._analyze_repo_nfet(repo, token="gh-token")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no clone URL configured"
    assert called["value"] is False


@pytest.mark.asyncio
async def test_analyze_repo_nfet_treats_control_character_clone_url_as_missing(
    monkeypatch,
) -> None:
    called = {"value": False}

    async def fake_build_graph_from_clone_url(*args, **kwargs):
        called["value"] = True
        return "graph"

    monkeypatch.setattr(
        repo_routes,
        "build_graph_from_clone_url",
        fake_build_graph_from_clone_url,
    )

    repo = SimpleNamespace(
        clone_url="https://github.com/owner/repo.git\r\nbad",
        full_name="owner/repo",
    )

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._analyze_repo_nfet(repo, token="gh-token")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no clone URL configured"
    assert called["value"] is False


@pytest.mark.asyncio
async def test_analyze_repo_nfet_rejects_invalid_clone_url_before_clone(
    monkeypatch,
) -> None:
    called = {"value": False}

    async def fake_build_graph_from_clone_url(*args, **kwargs):
        called["value"] = True
        return "graph"

    monkeypatch.setattr(
        repo_routes,
        "build_graph_from_clone_url",
        fake_build_graph_from_clone_url,
    )

    repo = SimpleNamespace(
        clone_url="http://github.com/owner/repo.git",
        full_name="owner/repo",
    )

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._analyze_repo_nfet(repo, token="gh-token")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has invalid clone URL configured"
    assert called["value"] is False


@pytest.mark.asyncio
async def test_analyze_repo_nfet_rejects_mismatched_clone_url_before_clone(
    monkeypatch,
) -> None:
    called = {"value": False}

    async def fake_build_graph_from_clone_url(*args, **kwargs):
        called["value"] = True
        return "graph"

    monkeypatch.setattr(
        repo_routes,
        "build_graph_from_clone_url",
        fake_build_graph_from_clone_url,
    )

    repo = SimpleNamespace(
        clone_url="https://github.com/other/repo.git",
        full_name="owner/repo",
    )

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._analyze_repo_nfet(repo, token="gh-token")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository clone URL does not match repository"
    assert called["value"] is False


@pytest.mark.asyncio
async def test_analyze_repo_nfet_treats_missing_clone_url_as_missing(monkeypatch) -> None:
    called = {"value": False}

    async def fake_build_graph_from_clone_url(*args, **kwargs):
        called["value"] = True
        return "graph"

    monkeypatch.setattr(
        repo_routes,
        "build_graph_from_clone_url",
        fake_build_graph_from_clone_url,
    )

    repo = SimpleNamespace(full_name="owner/repo")

    with pytest.raises(HTTPException) as exc_info:
        await repo_routes._analyze_repo_nfet(repo, token="gh-token")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Repository has no clone URL configured"
    assert called["value"] is False


@pytest.mark.asyncio
async def test_get_repo_nfet_summary_normalizes_malformed_repo_state(monkeypatch) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(
            id="repo-1",
            full_name="owner/repo",
        )

    async def fake_analyze_repo_nfet(repo, token, goal=None, target_file=None):
        repo_state = SimpleNamespace(
            phase=["observe"],
            global_kappa="0.3",
            global_sigma={"value": 0.4},
            global_es="0.5",
            total_nodes="8",
            total_edges=["12"],
            highest_stress_component={"name": "module.py"},
            highest_stress_value="0.9",
            hotspots={"bad": "payload"},
        )
        return object(), object(), repo_state

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)
    monkeypatch.setattr(repo_routes, "_analyze_repo_nfet", fake_analyze_repo_nfet)

    response = await repo_routes.get_repo_nfet_summary(
        "repo-id",
        current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert response.phase == ""
    assert response.global_kappa == 0.3
    assert response.global_sigma == 0.0
    assert response.global_es == 0.5
    assert response.total_nodes == 8
    assert response.total_edges == 0
    assert response.highest_stress_component == ""
    assert response.highest_stress_value == 0.9
    assert response.hotspot_count == 0
    assert response.top_hotspots == []


@pytest.mark.asyncio
async def test_get_repo_nfet_summary_allows_public_repo_without_github_token(
    monkeypatch,
) -> None:
    tokens: list[str | None] = []

    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(
            id="repo-1",
            full_name="owner/repo",
        )

    async def fake_analyze_repo_nfet(repo, token, goal=None, target_file=None):
        tokens.append(token)
        repo_state = SimpleNamespace(
            phase="stabilize",
            global_kappa=0.1,
            global_sigma=0.2,
            global_es=0.3,
            total_nodes=1,
            total_edges=0,
            highest_stress_component="README.md",
            highest_stress_value=0.4,
            hotspots=[],
        )
        return object(), object(), repo_state

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)
    monkeypatch.setattr(repo_routes, "_analyze_repo_nfet", fake_analyze_repo_nfet)

    response = await repo_routes.get_repo_nfet_summary(
        "repo-id",
        current_user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert tokens == [None]
    assert response.repo_id == "repo-1"
    assert response.phase == "stabilize"
    assert response.global_es == 0.3


@pytest.mark.asyncio
async def test_get_repo_nfet_summary_ignores_newline_github_tokens(monkeypatch) -> None:
    tokens: list[str | None] = []

    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(
            id="repo-1",
            full_name="owner/repo",
        )

    async def fake_analyze_repo_nfet(repo, token, goal=None, target_file=None):
        tokens.append(token)
        repo_state = SimpleNamespace(
            phase="observe",
            global_kappa=0.1,
            global_sigma=0.2,
            global_es=0.3,
            total_nodes=1,
            total_edges=0,
            highest_stress_component="app.py",
            highest_stress_value=0.4,
            hotspots=[],
        )
        return object(), object(), repo_state

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)
    monkeypatch.setattr(repo_routes, "_analyze_repo_nfet", fake_analyze_repo_nfet)

    response = await repo_routes.get_repo_nfet_summary(
        "repo-id",
        current_user=SimpleNamespace(
            id="user-1",
            github_token="gh-token\nX-Injected: bad",
        ),
        db=object(),
    )

    assert tokens == [None]
    assert response.repo_id == "repo-1"


@pytest.mark.asyncio
async def test_get_repo_nfet_candidates_normalizes_malformed_state_and_candidates(
    monkeypatch,
) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(
            id="repo-1",
            full_name="owner/repo",
        )

    async def fake_analyze_repo_nfet(repo, token, goal=None, target_file=None):
        repo_state = SimpleNamespace(
            phase=["observe"],
            global_es="0.5",
        )
        return "graph", _FakeController(), repo_state

    class _FakeController:
        def rank_interventions(self, *args, **kwargs):
            return {"bad": "payload"}

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)
    monkeypatch.setattr(repo_routes, "_analyze_repo_nfet", fake_analyze_repo_nfet)

    response = await repo_routes.get_repo_nfet_candidates(
        "repo-id",
        repo_routes.RepoNFETCandidatesRequest(),
        current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert response.phase == ""
    assert response.global_es == 0.5
    assert response.candidates == []


@pytest.mark.asyncio
async def test_get_repo_nfet_hotspots_normalizes_malformed_hotspots(monkeypatch) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(
            id="repo-1",
            full_name="owner/repo",
        )

    async def fake_analyze_repo_nfet(repo, token, goal=None, target_file=None):
        repo_state = SimpleNamespace(
            hotspots={"bad": "payload"},
        )
        return object(), object(), repo_state

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)
    monkeypatch.setattr(repo_routes, "_analyze_repo_nfet", fake_analyze_repo_nfet)

    response = await repo_routes.get_repo_nfet_hotspots(
        "repo-id",
        current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert response.hotspots == []


@pytest.mark.asyncio
async def test_simulate_repo_nfet_candidate_normalizes_malformed_simulation_fields(
    monkeypatch,
) -> None:
    async def fake_load_owned_repo(repo_id, current_user, db):
        return SimpleNamespace(
            id="repo-1",
            full_name="owner/repo",
        )

    async def fake_analyze_repo_nfet(repo, token, goal=None, target_file=None):
        return "graph", _FakeController(), SimpleNamespace()

    candidate = SimpleNamespace(
        candidate_id="candidate-1",
        kind="extract",
        to_dict=lambda: {
            "candidate_id": "candidate-1",
            "kind": "extract",
            "title": "Extract service",
            "description": "Move logic",
            "target_node_id": "node-1",
            "target_file_path": "app.py",
            "predicted_repo_es_delta": 0.1,
            "predicted_sigma_reduction": 0.2,
            "predicted_kappa_reduction": 0.3,
            "risk": 0.1,
            "cost": 0.2,
            "reversibility": 0.9,
            "confidence": 0.8,
            "score": 0.7,
            "reasons": ["reduce coupling"],
        },
    )

    class _FakeController:
        def rank_interventions(self, *args, **kwargs):
            return [candidate]

        def simulate_action(self, *args, **kwargs):
            return SimpleNamespace(
                before_component_es="0.1",
                after_component_es={"value": 0.2},
                before_repo_es="0.3",
                after_repo_es=["0.4"],
                predicted_phase=["stabilize"],
                narrative={"text": "ok"},
                candidate=candidate,
            )

    monkeypatch.setattr(repo_routes, "_load_owned_repo", fake_load_owned_repo)
    monkeypatch.setattr(repo_routes, "_analyze_repo_nfet", fake_analyze_repo_nfet)

    response = await repo_routes.simulate_repo_nfet_candidate(
        "repo-id",
        repo_routes.RepoNFETSimulationRequest(kind="extract"),
        current_user=SimpleNamespace(id="user-1", github_token="gh-token"),
        db=object(),
    )

    assert response.repo_id == "repo-1"
    assert response.before_component_es == 0.1
    assert response.after_component_es == 0.0
    assert response.before_repo_es == 0.3
    assert response.after_repo_es == 0.0
    assert response.predicted_phase == ""
    assert response.narrative == ""
    assert response.candidate.candidate_id == "candidate-1"


def test_candidate_to_response_normalizes_malformed_fields() -> None:
    candidate = SimpleNamespace(
        to_dict=lambda: {
            "candidate_id": ["candidate-1"],
            "kind": {"kind": "extract"},
            "title": ["Extract service"],
            "description": {"description": "Move logic"},
            "target_node_id": ["node-1"],
            "target_file_path": {"path": "app.py"},
            "predicted_repo_es_delta": " -1.5 ",
            "predicted_sigma_reduction": {"value": 0.2},
            "predicted_kappa_reduction": "0.4",
            "risk": " 0.1 ",
            "cost": ["0.2"],
            "reversibility": 0.9,
            "confidence": "0.8",
            "score": {"score": 12},
            "reasons": [" reduce coupling ", 7, "", None],
        }
    )

    response = repo_routes._candidate_to_response(candidate)

    assert response.candidate_id == ""
    assert response.kind == ""
    assert response.title == ""
    assert response.description == ""
    assert response.target_node_id == ""
    assert response.target_file_path == ""
    assert response.predicted_repo_es_delta == -1.5
    assert response.predicted_sigma_reduction == 0.0
    assert response.predicted_kappa_reduction == 0.4
    assert response.risk == 0.1
    assert response.cost == 0.0
    assert response.reversibility == 0.9
    assert response.confidence == 0.8
    assert response.score == 0.0
    assert response.reasons == ["reduce coupling"]


def test_component_to_response_normalizes_malformed_fields() -> None:
    component = SimpleNamespace(
        to_dict=lambda: {
            "node_id": ["node-1"],
            "name": {"name": "Service"},
            "file_path": ["app/service.py"],
            "kind": {"kind": "module"},
            "stress": " 1.5 ",
            "coupling": {"value": 0.4},
            "cohesion": "0.7",
            "complexity": ["2.0"],
            "cascade_depth": " 3 ",
            "impact_radius": {"value": 4},
            "fanin": 5.0,
            "fanout": ["6"],
            "betweenness": "0.9",
            "shared_state_edges": " 2 ",
            "cycle_detected": "yes",
            "sigma": {"value": 0.2},
            "kappa": "0.3",
            "gamma": ["0.4"],
            "es": 1.1,
            "risk_score": {"score": 4.2},
            "risk_level": ["high"],
            "reasons": [" unstable dependency ", 9, "", None],
        }
    )

    response = repo_routes._component_to_response(component)

    assert response.node_id == ""
    assert response.name == ""
    assert response.file_path == ""
    assert response.kind == ""
    assert response.stress == 1.5
    assert response.coupling == 0.0
    assert response.cohesion == 0.7
    assert response.complexity == 0.0
    assert response.cascade_depth == 3
    assert response.impact_radius == 0
    assert response.fanin == 5
    assert response.fanout == 0
    assert response.betweenness == 0.9
    assert response.shared_state_edges == 2
    assert response.cycle_detected is True
    assert response.sigma == 0.0
    assert response.kappa == 0.3
    assert response.gamma == 0.0
    assert response.es == 1.1
    assert response.risk_score == 0.0
    assert response.risk_level == ""
    assert response.reasons == ["unstable dependency"]
