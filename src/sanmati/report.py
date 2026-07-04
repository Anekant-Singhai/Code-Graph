from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sanmati.models import CodeGraph, Edge, Node
from sanmati.run import AnalysisRun


@dataclass(slots=True)
class ProjectSummary:
    project_name: str
    root: str
    file_count: int
    python_file_count: int
    function_count: int
    class_count: int
    dependency_count: int
    config_file_count: int
    project_kinds: list[str]
    data_kinds: list[str]
    entry_points: list[str]
    notable_dependencies: list[str]


def build_project_summary(graph: CodeGraph) -> ProjectSummary:
    files = graph.nodes_by_kind("file")
    dependencies = graph.nodes_by_kind("dependency")
    data_kinds = sorted(node.label for node in graph.nodes_by_kind("data_kind"))
    entry_points = _entry_point_labels(graph)
    notable_dependencies = sorted({node.label for node in dependencies})[:50]
    project_kinds = infer_project_kinds(graph, notable_dependencies, data_kinds)

    return ProjectSummary(
        project_name=Path(graph.root).name,
        root=str(graph.root),
        file_count=len(files),
        python_file_count=sum(1 for node in files if node.properties.get("category") == "python"),
        function_count=len(graph.nodes_by_kind("function")) + len(graph.nodes_by_kind("method")),
        class_count=len(graph.nodes_by_kind("class")),
        dependency_count=len(dependencies),
        config_file_count=sum(1 for node in files if node.properties.get("category") in {"config", "dependency-manifest"}),
        project_kinds=project_kinds,
        data_kinds=data_kinds,
        entry_points=entry_points,
        notable_dependencies=notable_dependencies,
    )


def infer_project_kinds(graph: CodeGraph, dependencies: list[str], data_kinds: list[str]) -> list[str]:
    dep_text = " ".join(dependencies).lower()
    file_labels = " ".join(node.label.lower() for node in graph.nodes_by_kind("file"))
    kinds: list[str] = []

    checks = [
        ("web-api", ["fastapi", "flask", "django", "starlette", "api"]),
        ("data-science", ["pandas", "numpy", "sklearn", "matplotlib", "notebook", "jupyter"]),
        ("machine-learning", ["torch", "tensorflow", "keras", "transformers", "tensor"]),
        ("cli-tool", ["argparse", "click", "typer"]),
        ("automation-script", ["subprocess", "powershell", ".ps1", ".sh"]),
        ("frontend", ["react", "vue", "vite", "next", ".tsx", ".jsx"]),
        ("containerized-service", ["dockerfile", "docker-compose"]),
        ("library-package", ["pyproject.toml", "setup.py", "setup.cfg"]),
    ]
    haystack = f"{dep_text} {file_labels} {' '.join(data_kinds).lower()}"
    for label, needles in checks:
        if any(needle in haystack for needle in needles):
            kinds.append(label)
    return kinds or ["general-software-project"]


def render_markdown_summary(
    graph: CodeGraph,
    summary: ProjectSummary,
    run: AnalysisRun | None = None,
    visualization_manifest: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"# Codebase Intelligence Report: {summary.project_name}")
    lines.append("")
    lines.append("## Project Snapshot")
    lines.append("")
    lines.append(f"- Root: `{summary.root}`")
    lines.append(f"- Files analyzed: {summary.file_count}")
    lines.append(f"- Python files: {summary.python_file_count}")
    lines.append(f"- Functions/methods: {summary.function_count}")
    lines.append(f"- Classes: {summary.class_count}")
    lines.append(f"- Dependencies detected: {summary.dependency_count}")
    lines.append(f"- Config/dependency files: {summary.config_file_count}")
    lines.append(f"- Project kind signals: {', '.join(summary.project_kinds)}")
    lines.append(f"- Data kind signals: {', '.join(summary.data_kinds) if summary.data_kinds else 'none detected'}")
    if run is not None:
        lines.append(f"- Run id: `{run.run_id}`")
        lines.append(f"- Run status: `{run.status}`")
        lines.append(f"- Skipped paths: {len(run.skipped_paths)}")
        lines.append(f"- Parse errors: {len(run.parse_errors)}")
    lines.append("")
    lines.append("## What This Project Appears To Be")
    lines.append("")
    lines.append(_purpose_paragraph(summary))
    lines.append("")
    lines.append("## Entry Points")
    lines.append("")
    if summary.entry_points:
        for item in summary.entry_points:
            lines.append(f"- `{item}`")
    else:
        lines.append("- No explicit entry point detected yet.")
    lines.append("")
    lines.append("## Dependencies")
    lines.append("")
    if summary.notable_dependencies:
        for item in summary.notable_dependencies[:30]:
            lines.append(f"- `{item}`")
    else:
        lines.append("- No imports or manifest dependencies detected.")
    lines.append("")
    lines.append("## Data Flow Signals")
    lines.append("")
    lines.extend(_data_flow_lines(graph))
    lines.append("")
    lines.append("## Conditional Calls")
    lines.append("")
    lines.extend(_conditional_call_lines(graph))
    lines.append("")
    lines.append("## File And Config Influence")
    lines.append("")
    lines.extend(_config_influence_lines(graph))
    lines.append("")
    lines.append("## Auditability")
    lines.append("")
    lines.extend(_audit_lines(run, visualization_manifest))
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        "This report is generated from deterministic static analysis. It is intentionally conservative: "
        "when a relationship cannot be proven from the parsed graph, it is left out instead of guessed."
    )
    lines.append("")
    return "\n".join(lines)


def _purpose_paragraph(summary: ProjectSummary) -> str:
    kinds = ", ".join(summary.project_kinds)
    data = ", ".join(summary.data_kinds) if summary.data_kinds else "no strong data format signal"
    return (
        f"The codebase currently looks like a {kinds} project. "
        f"It contains {summary.python_file_count} Python file(s), "
        f"{summary.function_count} callable unit(s), and uses {data}. "
        "A later LLM layer can turn this graph-backed snapshot into a richer architectural explanation."
    )


def _entry_point_labels(graph: CodeGraph) -> list[str]:
    labels: list[str] = []
    for edge in graph.edges_by_kind("ENTRY_POINT"):
        target = graph.nodes.get(edge.target)
        if target:
            qualified = target.properties.get("qualified_name") or target.label
            labels.append(str(qualified))
    return sorted(set(labels))


def _data_flow_lines(graph: CodeGraph) -> list[str]:
    lines: list[str] = []
    for edge in graph.edges_by_kind("USES_DATA_KIND"):
        source = graph.nodes.get(edge.source)
        target = graph.nodes.get(edge.target)
        if not source or not target:
            continue
        call = edge.properties.get("call", "unknown")
        lines.append(f"- `{_node_label(source)}` uses `{target.label}` via `{call}`.")
    return lines or ["- No direct data-kind usage detected yet."]


def _conditional_call_lines(graph: CodeGraph) -> list[str]:
    lines: list[str] = []
    for edge in graph.edges_by_kind("CALLS_UNDER_CONDITION"):
        source = graph.nodes.get(edge.source)
        target = graph.nodes.get(edge.target)
        if not source or not target:
            continue
        resolved = _resolved_call_target(graph, target.id)
        display_target = resolved or target
        condition = edge.properties.get("condition", "unknown condition")
        lines.append(f"- `{_node_label(source)}` calls `{_node_label(display_target)}` when `{condition}`.")
    return lines or ["- No conditional call relationships detected yet."]


def _resolved_call_target(graph: CodeGraph, call_target_id: str) -> Node | None:
    for edge in graph.edges_by_kind("RESOLVES_TO"):
        if edge.source == call_target_id:
            return graph.nodes.get(edge.target)
    return None


def _config_influence_lines(graph: CodeGraph) -> list[str]:
    lines: list[str] = []
    for edge in graph.edges_by_kind("DEPENDS_ON_CONFIG"):
        source = graph.nodes.get(edge.source)
        target = graph.nodes.get(edge.target)
        if not source or not target:
            continue
        literal = edge.properties.get("literal", target.label)
        lines.append(f"- `{_node_label(source)}` references config/data file `{target.label}` through `{literal}`.")
    return lines or ["- No direct config-file influence detected yet."]


def _audit_lines(run: AnalysisRun | None, visualization_manifest: dict[str, Any] | None) -> list[str]:
    lines: list[str] = []
    if run is None:
        lines.append("- No run ledger was attached to this export.")
    else:
        if run.skipped_paths:
            lines.append("- Skipped paths were recorded in `skipped_paths.json` and `analysis.sqlite`.")
            for item in run.skipped_paths[:10]:
                lines.append(f"- Skipped `{item.path}` because `{item.reason}` ({item.severity}).")
            if len(run.skipped_paths) > 10:
                lines.append(f"- {len(run.skipped_paths) - 10} additional skipped path(s) omitted from this summary.")
        else:
            lines.append("- No skipped paths were recorded.")
        if run.parse_errors:
            lines.append("- Parse errors were recorded in `parse_errors.json` and `analysis.sqlite`.")
            for item in run.parse_errors[:10]:
                lines.append(f"- Parse issue in `{item.path}` via `{item.parser}`: {item.message}")
            if len(run.parse_errors) > 10:
                lines.append(f"- {len(run.parse_errors) - 10} additional parse issue(s) omitted from this summary.")
        else:
            lines.append("- No parse errors were recorded.")

    if visualization_manifest:
        visualizations = visualization_manifest.get("visualizations", [])
        for item in visualizations:
            omitted = int(item.get("omitted_edges", 0))
            selected = int(item.get("selected_edges", 0))
            rendered = int(item.get("rendered_edges", 0))
            name = item.get("name", "visualization")
            if omitted:
                lines.append(f"- `{name}` rendered {rendered}/{selected} selected edge(s); {omitted} omitted by the bounded view.")
            else:
                lines.append(f"- `{name}` rendered all {rendered} selected edge(s).")
    else:
        lines.append("- No visualization manifest was provided.")
    lines.append("- LLM-ready evidence packets are exported under `llm_packets/`; these packets are bounded and citation-first.")
    return lines


def _node_label(node: Node) -> str:
    return str(node.properties.get("qualified_name") or node.label)
