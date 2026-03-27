"""FastAPI server for the Codey Structural Health Dashboard."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any


def _safe_round(val: float, digits: int = 4) -> float:
    """Round a float, replacing inf/nan with 0."""
    if math.isnan(val) or math.isinf(val):
        return 0.0
    return round(val, digits)

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from codey.graph.engine import CodebaseGraph
from codey.nfet.sweep import NFETSweep, SweepResult, Phase
from codey.nfet.health_db import HealthDatabase
from codey.autonomous.audit_db import AuditDatabase

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class DashboardState:
    """Shared state between the dashboard and the core engine."""

    def __init__(self) -> None:
        self.graph: Any = None
        self.sweep_engine: Any = None
        self.health_db: Any = None
        self.audit_db: Any = None
        self.monitor: Any = None
        self.last_sweep: Any = None
        self.connected_clients: set = set()

    async def broadcast(self, data: dict) -> None:
        payload = json.dumps(data)
        stale = []
        for ws in self.connected_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.connected_clients.discard(ws)


def create_app(state: DashboardState) -> FastAPI:
    app = FastAPI(title="Codey Structural Health Dashboard", version="1.0.0")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC_DIR / "index.html").read_text()

    @app.get("/api/status")
    async def api_status():
        sweep: SweepResult = state.last_sweep
        if sweep is None:
            return {"phase": "UNKNOWN", "kappa": 0, "sigma": 0, "es_score": 0, "node_count": 0, "edge_count": 0}
        graph: CodebaseGraph = state.graph
        return {
            "phase": sweep.phase.name,
            "kappa": round(sweep.kappa, 4),
            "sigma": round(sweep.sigma, 4),
            "es_score": round(sweep.es_score, 4),
            "node_count": graph.node_count if graph else 0,
            "edge_count": graph.edge_count if graph else 0,
        }

    @app.get("/api/stress")
    async def api_stress():
        sweep: SweepResult = state.last_sweep
        graph: CodebaseGraph = state.graph
        if sweep is None or graph is None:
            return {"components": []}

        components = []
        for comp_id, stress_val in sweep.top_stress_components:
            node_data = graph._graph.nodes.get(comp_id, {})
            name = node_data.get("name", comp_id[:12])
            fp = node_data.get("file_path", "")
            display = f"{Path(fp).parent.name}/{Path(fp).name}" if fp else name
            components.append({
                "id": comp_id,
                "name": f"{display}:{name}",
                "stress": _safe_round(stress_val, 4),
                "coupling": _safe_round(graph.coupling_score(node_data.get("file_path", "")), 4),
                "cohesion": _safe_round(graph.cohesion_score(node_data.get("file_path", "")), 4),
                "cascade_depth": graph.cascade_depth(comp_id),
            })

        # Also add more components beyond top 5 to fill the table
        # Normalize raw stress the same way the sweep does: s/(s+10)
        _STRESS_SCALE = 10.0
        all_stress = graph.get_high_stress_components(threshold=0.3)
        seen = {c["id"] for c in components}
        for comp_id, raw_stress in all_stress:
            stress_val = raw_stress / (raw_stress + _STRESS_SCALE) if raw_stress > 0 else 0.0
            if comp_id in seen:
                continue
            if len(components) >= 10:
                break
            node_data = graph._graph.nodes.get(comp_id, {})
            name = node_data.get("name", comp_id[:12])
            fp = node_data.get("file_path", "")
            display = f"{Path(fp).parent.name}/{Path(fp).name}" if fp else name
            components.append({
                "id": comp_id,
                "name": f"{display}:{name}",
                "stress": _safe_round(stress_val, 4),
                "coupling": _safe_round(graph.coupling_score(node_data.get("file_path", "")), 4),
                "cohesion": _safe_round(graph.cohesion_score(node_data.get("file_path", "")), 4),
                "cascade_depth": graph.cascade_depth(comp_id),
            })
            seen.add(comp_id)

        return {"components": components}

    @app.get("/api/history")
    async def api_history(hours: int = 24):
        if state.health_db is None:
            return {"history": []}
        records = state.health_db.get_history(hours=hours)
        return {
            "history": [
                {
                    "timestamp": r.get("timestamp", ""),
                    "es_score": round(r.get("es_score", 0), 4),
                    "kappa": round(r.get("kappa", 0), 4),
                    "sigma": round(r.get("sigma", 0), 4),
                    "phase": r.get("phase", ""),
                }
                for r in records
            ]
        }

    @app.get("/api/changes")
    async def api_changes(limit: int = 20):
        if state.audit_db is None:
            return {"changes": []}
        records = state.audit_db.get_recent(limit=limit)
        return {
            "changes": [
                {
                    "timestamp": r.get("timestamp", ""),
                    "trigger": r.get("trigger_condition", ""),
                    "component": r.get("component_affected", ""),
                    "stress_before": r.get("stress_before", 0),
                    "stress_after": r.get("stress_after", 0),
                    "es_before": r.get("es_before", 0),
                    "es_after": r.get("es_after", 0),
                    "rolled_back": bool(r.get("rolled_back", 0)),
                }
                for r in records
            ]
        }

    @app.get("/api/component/{component_id}")
    async def api_component(component_id: str):
        graph: CodebaseGraph = state.graph
        if graph is None:
            return {"error": "No graph available"}
        if component_id not in graph._graph:
            return {"error": f"Component '{component_id}' not found"}

        node_data = graph._graph.nodes[component_id]
        fp = node_data.get("file_path", "")
        successors = list(graph._graph.successors(component_id))
        predecessors = list(graph._graph.predecessors(component_id))
        betweenness = graph.betweenness_centrality().get(component_id, 0.0)

        return {
            "id": component_id,
            "name": node_data.get("name", ""),
            "kind": node_data.get("kind", ""),
            "file_path": fp,
            "stress": _safe_round(graph.stress_score(component_id), 4),
            "coupling": _safe_round(graph.coupling_score(fp), 4),
            "cohesion": _safe_round(graph.cohesion_score(fp), 4),
            "cascade_depth": graph.cascade_depth(component_id),
            "betweenness": round(betweenness, 4),
            "impact_radius": len(graph.impact_radius(component_id)),
            "dependencies": [{"id": s, "name": graph._graph.nodes.get(s, {}).get("name", s)} for s in successors[:20]],
            "dependents": [{"id": p, "name": graph._graph.nodes.get(p, {}).get("name", p)} for p in predecessors[:20]],
        }

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        state.connected_clients.add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            state.connected_clients.discard(ws)

    return app


def run_dashboard(state: DashboardState, host: str = "0.0.0.0", port: int = 7000) -> None:
    app = create_app(state)
    uvicorn.run(app, host=host, port=port, log_level="info")
