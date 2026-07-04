from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from sanmati.llm_packets import EvidencePacketBuilder
from sanmati.models import CodeGraph, Edge
from sanmati.queries import DATA_FLOW_EDGE_KINDS, CodeQuery
from sanmati.report import ProjectSummary, render_markdown_summary
from sanmati.run import AnalysisRun
from sanmati.storage import AnalysisStore


@dataclass(slots=True)
class VisualizationMeta:
    name: str
    edge_kinds: list[str]
    selected_edges: int
    rendered_edges: int
    omitted_edges: int
    limit: int

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "edge_kinds": self.edge_kinds,
            "selected_edges": self.selected_edges,
            "rendered_edges": self.rendered_edges,
            "omitted_edges": self.omitted_edges,
            "limit": self.limit,
        }


def export_report(graph: CodeGraph, summary: ProjectSummary, out_dir: str | Path, run: AnalysisRun | None = None) -> Path:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    _clear_report_artifacts(out_path)

    (out_path / "graph.json").write_text(json.dumps(graph.to_dict(), indent=2), encoding="utf-8")
    (out_path / "graph_summary.json").write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")

    module_graph, module_meta = render_module_mermaid(graph)
    call_graph, call_meta = render_call_mermaid(graph)
    manifest = {
        "visualizations": [module_meta.to_dict(), call_meta.to_dict()],
        "note": "Mermaid files are bounded slices. Use graph.json or analysis.sqlite for complete graph data.",
    }

    if run is not None:
        (out_path / "run.json").write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
        (out_path / "skipped_paths.json").write_text(
            json.dumps([item.to_dict() for item in run.skipped_paths], indent=2),
            encoding="utf-8",
        )
        (out_path / "parse_errors.json").write_text(
            json.dumps([item.to_dict() for item in run.parse_errors], indent=2),
            encoding="utf-8",
        )
        AnalysisStore(out_path / "analysis.sqlite").write(graph, summary, run)

    (out_path / "visualization_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_path / "summary.md").write_text(render_markdown_summary(graph, summary, run, manifest), encoding="utf-8")
    (out_path / "module_graph.mmd").write_text(module_graph, encoding="utf-8")
    (out_path / "call_graph.mmd").write_text(call_graph, encoding="utf-8")
    export_slices(graph, out_path / "slices")
    export_llm_packets(graph, run, out_path / "llm_packets")
    return out_path


def _clear_report_artifacts(out_path: Path) -> None:
    files = {
        "analysis.sqlite",
        "analysis.sqlite-shm",
        "analysis.sqlite-wal",
        "call_graph.mmd",
        "graph.json",
        "graph_summary.json",
        "module_graph.mmd",
        "parse_errors.json",
        "run.json",
        "skipped_paths.json",
        "summary.md",
        "visualization_manifest.json",
    }
    dirs = {"slices", "llm_packets"}
    resolved_out = out_path.resolve()
    for name in files:
        path = (out_path / name).resolve()
        if path.exists() and path.parent == resolved_out:
            path.unlink()
    for name in dirs:
        path = (out_path / name).resolve()
        if path.exists() and path.is_dir() and path.parent == resolved_out:
            shutil.rmtree(path)


def render_module_mermaid(graph: CodeGraph) -> tuple[str, VisualizationMeta]:
    lines = ["flowchart LR"]
    edge_kinds = {"DEFINES_MODULE", "IMPORTS", "CONTAINS", "DEPENDS_ON_CONFIG", "REPRESENTS_DATA_KIND"}
    selected = [edge for edge in graph.edges if edge.kind in edge_kinds]
    rendered = _append_edges(lines, graph, selected, limit=220)
    if len(lines) == 1:
        lines.append('  empty["No module relationships detected"]')
    meta = VisualizationMeta(
        name="module_graph",
        edge_kinds=sorted(edge_kinds),
        selected_edges=len(selected),
        rendered_edges=rendered,
        omitted_edges=max(len(selected) - rendered, 0),
        limit=220,
    )
    return "\n".join(lines) + "\n", meta


def render_call_mermaid(graph: CodeGraph) -> tuple[str, VisualizationMeta]:
    lines = ["flowchart TD"]
    edge_kinds = {"CALLS_RESOLVED", "CALLS_UNDER_CONDITION", "USES_DATA_KIND", "WRITES_TO"}
    selected = [edge for edge in graph.edges if edge.kind in edge_kinds]
    rendered = _append_edges(lines, graph, selected, limit=250)
    if len(lines) == 1:
        lines.append('  empty["No call relationships detected"]')
    meta = VisualizationMeta(
        name="call_graph",
        edge_kinds=sorted(edge_kinds),
        selected_edges=len(selected),
        rendered_edges=rendered,
        omitted_edges=max(len(selected) - rendered, 0),
        limit=250,
    )
    return "\n".join(lines) + "\n", meta


def export_slices(graph: CodeGraph, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    slices = {
        "entry_points": {"ENTRY_POINT", "CALLS_RESOLVED"},
        "config_influence": {"DEPENDS_ON_CONFIG", "REFERENCES_FILE", "DECLARES_CONFIG_KEY"},
        "external_dependencies": {"IMPORTS", "DECLARES_DEPENDENCY"},
        "data_flow": {"USES_DATA_KIND", "DATA_FLOWS_TO", "PASSES_ARGUMENT", "RETURNS_VALUE", "ITERATES_TO"},
    }
    for name, kinds in slices.items():
        edges = [edge for edge in graph.edges if edge.kind in kinds]
        payload = {
            "name": name,
            "edge_kinds": sorted(kinds),
            "nodes": _nodes_for_edges(graph, edges),
            "edges": [edge.to_dict() for edge in edges],
        }
        (out_dir / f"{name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        lines = ["flowchart LR"]
        _append_edges(lines, graph, edges, limit=180)
        if len(lines) == 1:
            lines.append(f'  empty["No {name} relationships detected"]')
        (out_dir / f"{name}.mmd").write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_llm_packets(graph: CodeGraph, run: AnalysisRun | None, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    builder = EvidencePacketBuilder(graph, run)
    query = CodeQuery(graph)
    packets = [
        builder.project_overview_packet(),
        builder.entry_point_packet(),
        builder.diagnostics_packet(),
    ]
    for entry_point in [node for node in query.get_entry_points() if node.kind in {"function", "method"}][:10]:
        symbol = str(entry_point.properties.get("qualified_name") or entry_point.id)
        packets.append(builder.function_packet(symbol))

    data_sources = _data_flow_sources(graph)[:10]
    for source_id in data_sources:
        packets.append(builder.data_flow_packet(source_id))

    manifest = {
        "principle": "LLMs explain and connect evidence; deterministic graph facts remain the source of truth.",
        "packet_count": len(packets),
        "packets": [
            {
                "packet_id": packet.packet_id,
                "kind": packet.kind,
                "subject": packet.subject,
                "path": f"{packet.packet_id.replace(':', '__')}.json",
                "warnings": packet.warnings,
            }
            for packet in packets
        ],
    }
    for packet in packets:
        filename = f"{packet.packet_id.replace(':', '__')}.json"
        (out_dir / filename).write_text(json.dumps(packet.to_dict(), indent=2), encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _data_flow_sources(graph: CodeGraph) -> list[str]:
    source_ids: list[str] = []
    seen: set[str] = set()
    for edge in graph.edges:
        if edge.kind not in DATA_FLOW_EDGE_KINDS:
            continue
        if edge.source in seen:
            continue
        seen.add(edge.source)
        source_ids.append(edge.source)
    return source_ids


def _nodes_for_edges(graph: CodeGraph, edges: list[Edge]) -> list[dict[str, object]]:
    node_ids = {edge.source for edge in edges} | {edge.target for edge in edges}
    return [graph.nodes[node_id].to_dict() for node_id in sorted(node_ids) if node_id in graph.nodes]


def _append_edges(lines: list[str], graph: CodeGraph, edges: list[Edge], *, limit: int) -> int:
    seen_nodes: set[str] = set()
    rendered = 0
    for edge in edges[:limit]:
        source = graph.nodes.get(edge.source)
        target = graph.nodes.get(edge.target)
        if not source or not target:
            continue
        source_id = _mermaid_id(edge.source)
        target_id = _mermaid_id(edge.target)
        if source_id not in seen_nodes:
            lines.append(f'  {source_id}["{_escape(source.kind + ": " + source.label)}"]')
            seen_nodes.add(source_id)
        if target_id not in seen_nodes:
            lines.append(f'  {target_id}["{_escape(target.kind + ": " + target.label)}"]')
            seen_nodes.add(target_id)
        label = edge.kind if edge.kind != "CALLS_UNDER_CONDITION" else "CALLS IF"
        lines.append(f"  {source_id} -- {label} --> {target_id}")
        rendered += 1
    omitted = len(edges) - rendered
    if omitted > 0:
        lines.append(f'  omitted["{omitted} edge(s) omitted from this bounded Mermaid view"]')
    return rendered


def _mermaid_id(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if clean and clean[0].isdigit():
        clean = "n_" + clean
    return clean or "node"


def _escape(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ")[:90]
