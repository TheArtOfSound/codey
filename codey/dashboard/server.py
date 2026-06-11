"""FastAPI server for the Codey Structural Health Dashboard."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any


def _safe_round(val: Any, digits: int = 4) -> float:
    """Round a numeric value, replacing malformed/inf/nan values with 0."""
    if isinstance(val, bool):
        return 0.0
    try:
        metric = float(val)
    except OverflowError:
        return 0.0
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(metric) or math.isinf(metric):
        return 0.0
    return round(metric, digits)


def _safe_float(val: Any) -> float:
    """Return a finite numeric value, replacing malformed/inf/nan values with 0."""
    if isinstance(val, bool):
        return 0.0
    try:
        metric = float(val)
    except (OverflowError, TypeError, ValueError):
        return 0.0
    return metric if math.isfinite(metric) else 0.0


def _safe_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            metric = float(val)
        except (OverflowError, TypeError, ValueError):
            return False
        return math.isfinite(metric) and metric != 0.0
    if isinstance(val, bytes):
        val = val.decode("utf-8", errors="replace")
    if isinstance(val, str):
        normalized = val.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"", "0", "false", "no", "off"}:
            return False
    return False


def _dashboard_text(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return val if isinstance(val, str) else str(val)


def _format_history_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        record = {}
    return {
        "timestamp": _dashboard_text(record.get("timestamp", "")),
        "es_score": _safe_round(record.get("es_score", 0), 4),
        "kappa": _safe_round(record.get("kappa", 0), 4),
        "sigma": _safe_round(record.get("sigma", 0), 4),
        "phase": _dashboard_text(record.get("phase", "")),
    }


def _format_change_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        record = {}
    return {
        "timestamp": _dashboard_text(record.get("timestamp", "")),
        "trigger": _dashboard_text(record.get("trigger_condition", "")),
        "component": _dashboard_text(record.get("component_affected", "")),
        "stress_before": _safe_float(record.get("stress_before", 0)),
        "stress_after": _safe_float(record.get("stress_after", 0)),
        "es_before": _safe_float(record.get("es_before", 0)),
        "es_after": _safe_float(record.get("es_after", 0)),
        "rolled_back": _safe_bool(record.get("rolled_back", 0)),
    }


def _safe_top_stress_components(
    value: Any, limit: int | None = None
) -> list[tuple[str, float]]:
    if not isinstance(value, (list, tuple)):
        return []

    components: list[tuple[str, float]] = []
    for item in value:
        if isinstance(item, dict):
            component_id = item.get("id") or item.get("component") or item.get("name")
            stress_value = item.get("stress")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            component_id = item[0]
            stress_value = item[1]
        else:
            continue

        if not isinstance(component_id, str) or not component_id:
            continue

        components.append((component_id, _safe_round(stress_value)))
        if limit is not None and len(components) >= limit:
            break

    return components


def _normalize_dashboard_stress(value: Any, scale: float = 10.0) -> float:
    try:
        raw = float(value)
    except (OverflowError, TypeError, ValueError):
        return 0.0
    if not math.isfinite(raw):
        return 1.0 if raw > 0 else 0.0
    try:
        scale = float(scale)
    except (OverflowError, TypeError, ValueError):
        scale = 10.0
    if not math.isfinite(scale) or scale <= 0:
        scale = 10.0
    return raw / (raw + scale) if raw > 0 else 0.0


def _dashboard_json_safe(value: Any, _seen: set[int] | None = None) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if _seen is None:
        _seen = set()
    if isinstance(value, dict):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return {
                str(key): _dashboard_json_safe(item, _seen)
                for key, item in value.items()
            }
        finally:
            _seen.remove(value_id)
    if isinstance(value, (set, frozenset)):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return [
                _dashboard_json_safe(item, _seen)
                for item in sorted(
                    value,
                    key=lambda item: (type(item).__name__, repr(item)),
                )
            ]
        finally:
            _seen.remove(value_id)
    if isinstance(value, (list, tuple)):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return [_dashboard_json_safe(item, _seen) for item in value]
        finally:
            _seen.remove(value_id)
    return str(value)


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
        payload = json.dumps(_dashboard_json_safe(data), allow_nan=False)
        stale = []
        for ws in list(self.connected_clients):
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
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/status")
    async def api_status():
        sweep: SweepResult = state.last_sweep
        if sweep is None:
            return {"phase": "UNKNOWN", "kappa": 0, "sigma": 0, "es_score": 0, "node_count": 0, "edge_count": 0}
        graph: CodebaseGraph = state.graph
        return {
            "phase": sweep.phase.name,
            "kappa": _safe_round(sweep.kappa, 4),
            "sigma": _safe_round(sweep.sigma, 4),
            "es_score": _safe_round(sweep.es_score, 4),
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
        for comp_id, stress_val in _safe_top_stress_components(
            sweep.top_stress_components
        ):
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
            stress_val = _normalize_dashboard_stress(raw_stress, _STRESS_SCALE)
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
                _format_history_record(r)
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
                _format_change_record(r)
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
            "betweenness": _safe_round(betweenness, 4),
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
