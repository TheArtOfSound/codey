"""Codey CLI — Network-Aware Autonomous Coding Intelligence."""

from __future__ import annotations

import sys
import time
import json
import logging
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live

from codey.parser import LanguageParser, parse_directory
from codey.graph import CodebaseGraph
from codey.nfet import NFETSweep, HealthDatabase, Phase
from codey.autonomous import AutonomousMonitor, AuditDatabase
from codey.dashboard.server import DashboardState, run_dashboard

console = Console()
logger = logging.getLogger("codey")


def _phase_color(phase: Phase) -> str:
    return {"RIDGE": "green", "CAUTION": "yellow", "CRITICAL": "red"}[phase.name]


def _build_graph(project_path: Path) -> tuple[CodebaseGraph, float]:
    """Parse project and build the codebase graph. Returns (graph, elapsed_seconds)."""
    console.print(f"[bold]Scanning[/bold] {project_path} ...")
    t0 = time.time()
    nodes, edges = parse_directory(project_path)
    graph = CodebaseGraph()
    graph.build_from_nodes_edges(nodes, edges)
    elapsed = time.time() - t0
    console.print(
        f"  [dim]{graph.node_count} nodes, {graph.edge_count} edges "
        f"in {elapsed:.2f}s[/dim]"
    )
    return graph, elapsed


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def main(verbose: bool):
    """Codey — Network-Aware Autonomous Coding Intelligence powered by NFET."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@main.command()
@click.argument("project_path", type=click.Path(exists=True, file_okay=False))
def scan(project_path: str):
    """Scan a project and display structural health summary."""
    path = Path(project_path).resolve()
    graph, _ = _build_graph(path)

    sweep = NFETSweep()
    sweep.calibrate(graph)
    result = sweep.run(graph)

    # Phase banner
    color = _phase_color(result.phase)
    phase_text = Text(f"  {result.phase.name}  ", style=f"bold white on {color}")
    console.print()
    console.print(Panel(phase_text, title="Codebase Phase", expand=False))
    console.print()

    # Metrics table
    metrics = Table(title="NFET Metrics", show_header=True, header_style="bold cyan")
    metrics.add_column("Metric", style="dim")
    metrics.add_column("Value", justify="right")
    metrics.add_column("Description")
    metrics.add_row("ES Score", f"{result.es_score:.3f}", "Equilibrium Score (0-1)")
    metrics.add_row("κ (kappa)", f"{result.kappa:.3f}", "Coupling density")
    metrics.add_row("σ (sigma)", f"{result.sigma:.3f}", "Distance to cascade boundary")
    metrics.add_row("Nodes", str(result.total_nodes), "Components in graph")
    metrics.add_row("Edges", str(result.total_edges), "Dependencies in graph")
    metrics.add_row("Mean Coupling", f"{result.mean_coupling:.3f}", "Average module coupling")
    metrics.add_row("Mean Cohesion", f"{result.mean_cohesion:.3f}", "Average module cohesion")
    console.print(metrics)
    console.print()

    # Top stress components
    if result.top_stress_components:
        stress_table = Table(
            title="Highest-Stress Components",
            show_header=True,
            header_style="bold red",
        )
        stress_table.add_column("Component", style="bold")
        stress_table.add_column("Stress", justify="right")
        stress_table.add_column("Cascade Depth", justify="right")
        for comp_id, stress_val in result.top_stress_components:
            depth = graph.cascade_depth(comp_id)
            scolor = "green" if stress_val < 0.4 else "yellow" if stress_val < 0.7 else "red"
            node_data = graph._graph.nodes.get(comp_id, {})
            name = node_data.get("name", comp_id[:12])
            fp = node_data.get("file_path", "")
            # Show relative path + name for clarity
            display = f"{Path(fp).parent.name}/{Path(fp).name}:{name}" if fp else name
            stress_table.add_row(display, f"[{scolor}]{stress_val:.3f}[/{scolor}]", str(depth))
        console.print(stress_table)
    else:
        console.print("[dim]No high-stress components detected.[/dim]")

    console.print()


@main.command()
@click.argument("project_path", type=click.Path(exists=True, file_okay=False))
@click.option("--component", "-c", help="Show details for a specific component")
def analyze(project_path: str, component: str | None):
    """Deep structural analysis of a project or component."""
    path = Path(project_path).resolve()
    graph, _ = _build_graph(path)

    sweep = NFETSweep()
    sweep.calibrate(graph)
    result = sweep.run(graph)

    if component:
        # Detailed component analysis
        if component not in graph._graph:
            console.print(f"[red]Component '{component}' not found in graph.[/red]")
            sys.exit(1)

        node_data = graph._graph.nodes[component]
        stress = graph.stress_score(component)
        coupling = graph.coupling_score(component)
        cohesion = graph.cohesion_score(component)
        depth = graph.cascade_depth(component)
        radius = graph.impact_radius(component)
        betweenness = graph.betweenness_centrality().get(component, 0.0)

        panel_content = (
            f"[bold]File:[/bold] {node_data.get('file_path', 'N/A')}\n"
            f"[bold]Kind:[/bold] {node_data.get('kind', 'N/A')}\n"
            f"[bold]Stress:[/bold] {stress:.3f}\n"
            f"[bold]Coupling:[/bold] {coupling:.3f}\n"
            f"[bold]Cohesion:[/bold] {cohesion:.3f}\n"
            f"[bold]Cascade Depth:[/bold] {depth}\n"
            f"[bold]Impact Radius:[/bold] {len(radius)} components\n"
            f"[bold]Betweenness:[/bold] {betweenness:.3f}\n"
        )

        # Dependencies
        successors = list(graph._graph.successors(component))
        predecessors = list(graph._graph.predecessors(component))
        panel_content += f"\n[bold]Depends on:[/bold] {len(successors)} components\n"
        for s in successors[:10]:
            panel_content += f"  → {s}\n"
        if len(successors) > 10:
            panel_content += f"  ... and {len(successors) - 10} more\n"

        panel_content += f"\n[bold]Depended on by:[/bold] {len(predecessors)} components\n"
        for p in predecessors[:10]:
            panel_content += f"  ← {p}\n"
        if len(predecessors) > 10:
            panel_content += f"  ... and {len(predecessors) - 10} more\n"

        console.print(Panel(panel_content, title=f"Component: {component}", expand=False))
    else:
        # Full project analysis — show all file-level nodes sorted by stress
        file_nodes = [
            n for n, d in graph._graph.nodes(data=True) if d.get("kind") == "file"
        ]
        file_stress = []
        for fn in file_nodes:
            s = graph.stress_score(fn)
            file_stress.append((fn, s))
        import math
        file_stress.sort(key=lambda x: x[1] if math.isfinite(x[1]) else 1e9, reverse=True)

        table = Table(
            title=f"All Modules — {result.phase.name} Phase (ES={result.es_score:.3f})",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Module", style="bold", max_width=60)
        table.add_column("Stress", justify="right")
        table.add_column("Coupling", justify="right")
        table.add_column("Cohesion", justify="right")
        table.add_column("Cascade", justify="right")

        import math
        for fn, s in file_stress[:30]:
            node_data = graph._graph.nodes.get(fn, {})
            fp = node_data.get("file_path", fn)
            # Show path relative to project root
            try:
                display = str(Path(fp).relative_to(path))
            except ValueError:
                display = Path(fp).name if fp else fn[:12]
            scolor = "green" if s < 0.4 else "yellow" if s < 0.7 else "red"
            stress_str = f"{s:.3f}" if math.isfinite(s) else ">999"
            c = graph.coupling_score(fp)
            h = graph.cohesion_score(fp)
            d = graph.cascade_depth(fn)
            table.add_row(
                display, f"[{scolor}]{stress_str}[/{scolor}]",
                f"{c:.1f}", f"{h:.3f}", str(d),
            )

        console.print(table)


@main.command()
@click.argument("project_path", type=click.Path(exists=True, file_okay=False))
@click.option("--port", "-p", default=7000, help="Dashboard port")
@click.option("--db-dir", default=None, help="Directory for health/audit databases")
def dashboard(project_path: str, port: int, db_dir: str | None):
    """Launch the structural health dashboard."""
    path = Path(project_path).resolve()
    db_path = Path(db_dir) if db_dir else path / ".codey"
    db_path.mkdir(parents=True, exist_ok=True)

    graph, _ = _build_graph(path)
    sweep = NFETSweep()
    sweep.calibrate(graph)

    health_db = HealthDatabase(str(db_path / "codey_health.db"))
    audit_db = AuditDatabase(str(db_path / "codey_audit.db"))

    # Initial sweep
    result = sweep.run(graph)
    health_db.log_sweep(result)

    state = DashboardState()
    state.graph = graph
    state.sweep_engine = sweep
    state.health_db = health_db
    state.audit_db = audit_db
    state.last_sweep = result

    color = _phase_color(result.phase)
    console.print(
        f"\n[bold]Codey Dashboard[/bold] — "
        f"[{color}]{result.phase.name}[/{color}] "
        f"(ES={result.es_score:.3f})"
    )
    console.print(f"[dim]http://localhost:{port}[/dim]\n")

    run_dashboard(state, port=port)


@main.command()
@click.argument("project_path", type=click.Path(exists=True, file_okay=False))
@click.option("--db-dir", default=None, help="Directory for health/audit databases")
def monitor(project_path: str, db_dir: str | None):
    """Start autonomous monitoring mode."""
    path = Path(project_path).resolve()
    db_path = Path(db_dir) if db_dir else path / ".codey"
    db_path.mkdir(parents=True, exist_ok=True)

    graph, _ = _build_graph(path)
    sweep = NFETSweep()
    sweep.calibrate(graph)

    health_db = HealthDatabase(str(db_path / "codey_health.db"))
    audit_db = AuditDatabase(str(db_path / "codey_audit.db"))

    mon = AutonomousMonitor(
        graph=graph,
        sweep_engine=sweep,
        audit_db=audit_db,
        health_db=health_db,
    )

    result = sweep.run(graph)
    color = _phase_color(result.phase)
    console.print(
        f"\n[bold]Codey Autonomous Monitor[/bold] — "
        f"[{color}]{result.phase.name}[/{color}] "
        f"(ES={result.es_score:.3f})"
    )
    console.print(f"[dim]Watching {path} for changes...[/dim]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    mon.start(path)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopping monitor...[/dim]")
        mon.stop()
        console.print("[bold]Monitor stopped.[/bold]")


@main.command()
@click.argument("project_path", type=click.Path(exists=True, file_okay=False))
@click.option("--output", "-o", default=None, help="Output JSON file")
def export(project_path: str, output: str | None):
    """Export codebase graph and NFET analysis as JSON."""
    path = Path(project_path).resolve()
    graph, _ = _build_graph(path)

    sweep = NFETSweep()
    sweep.calibrate(graph)
    result = sweep.run(graph)

    data = {
        "project": str(path),
        "phase": result.phase.name,
        "es_score": result.es_score,
        "kappa": result.kappa,
        "sigma": result.sigma,
        "total_nodes": result.total_nodes,
        "total_edges": result.total_edges,
        "mean_coupling": result.mean_coupling,
        "mean_cohesion": result.mean_cohesion,
        "top_stress_components": [
            {"id": c, "stress": s} for c, s in result.top_stress_components
        ],
        "nodes": [
            {
                "id": n,
                "kind": d.get("kind", ""),
                "name": d.get("name", ""),
                "file_path": d.get("file_path", ""),
                "stress": graph.stress_score(n),
            }
            for n, d in graph._graph.nodes(data=True)
        ],
    }

    if output:
        out_path = Path(output)
        out_path.write_text(json.dumps(data, indent=2))
        console.print(f"[bold]Exported to {out_path}[/bold]")
    else:
        console.print_json(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
