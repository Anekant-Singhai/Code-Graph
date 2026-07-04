from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sanmati.models import CodeGraph, Edge, Node
from sanmati.queries import CodeQuery
from sanmati.run import AnalysisRun


JsonDict = dict[str, Any]


@dataclass(slots=True)
class EvidenceCitation:
    citation_id: str
    node_id: str | None = None
    edge_id: str | None = None
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    confidence: float | None = None
    parser: str | None = None
    reason: str | None = None
    evidence_text: str | None = None

    def to_dict(self) -> JsonDict:
        return {
            "citation_id": self.citation_id,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "confidence": self.confidence,
            "parser": self.parser,
            "reason": self.reason,
            "evidence_text": self.evidence_text,
        }


@dataclass(slots=True)
class EvidencePacket:
    packet_id: str
    kind: str
    subject: str
    instructions: str
    limits: JsonDict
    facts: list[str] = field(default_factory=list)
    nodes: list[JsonDict] = field(default_factory=list)
    edges: list[JsonDict] = field(default_factory=list)
    citations: list[EvidenceCitation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "packet_id": self.packet_id,
            "kind": self.kind,
            "subject": self.subject,
            "instructions": self.instructions,
            "limits": self.limits,
            "facts": self.facts,
            "nodes": self.nodes,
            "edges": self.edges,
            "citations": [citation.to_dict() for citation in self.citations],
            "warnings": self.warnings,
        }


class EvidencePacketBuilder:
    DEFAULT_INSTRUCTIONS = (
        "Use only the supplied facts, nodes, edges, and citations. Explain and connect evidence; "
        "do not invent missing call relationships, data flow, or project purpose. Mention uncertainty "
        "when confidence is low or relationships are unresolved."
    )

    def __init__(self, graph: CodeGraph, run: AnalysisRun | None = None) -> None:
        self.graph = graph
        self.run = run
        self.query = CodeQuery(graph)

    def project_overview_packet(self, *, max_nodes: int = 80, max_edges: int = 160) -> EvidencePacket:
        edges = self._bounded_edges(
            [edge for edge in self.graph.edges if edge.kind in {"CONTAINS", "ENTRY_POINT", "IMPORTS", "DEPENDS_ON_CONFIG"}],
            max_edges,
        )
        nodes = self._nodes_for_edges(edges)[:max_nodes]
        facts = [
            f"Graph has {len(self.graph.nodes)} node(s) and {len(self.graph.edges)} edge(s).",
            f"Entry points detected: {len(self.query.get_entry_points())}.",
            f"Unresolved calls detected: {len(self.query.find_unresolved_calls())}.",
        ]
        if self.run:
            facts.append(f"Run {self.run.run_id} status is {self.run.status}.")
            facts.append(f"Skipped paths: {len(self.run.skipped_paths)}; parse errors: {len(self.run.parse_errors)}.")
        return self._packet("project_overview", "project", nodes, edges, facts, max_nodes, max_edges)

    def entry_point_packet(self, *, max_edges_per_entry: int = 80) -> EvidencePacket:
        entry_points = self.query.get_entry_points()
        nodes: list[Node] = list(entry_points)
        edges: list[Edge] = []
        for entry in entry_points:
            callees = [edge for edge in self.graph.edges if edge.source == entry.id and edge.kind == "CALLS_RESOLVED"]
            edges.extend(callees[:max_edges_per_entry])
        nodes = self._dedupe_nodes([*nodes, *self._nodes_for_edges(edges)])
        facts = [f"Entry point `{self._node_label(node)}` was detected." for node in entry_points]
        return self._packet("entry_points", "entry points", nodes, edges, facts, len(nodes), max_edges_per_entry * max(len(entry_points), 1))

    def function_packet(self, symbol: str, *, max_edges: int = 120) -> EvidencePacket:
        function = self._find_callable(symbol)
        if function is None:
            packet = self._packet("function", symbol, [], [], [], 0, max_edges)
            packet.warnings.append(f"Function or method was not found: {symbol}")
            return packet
        related_edges = [
            edge
            for edge in self.graph.edges
            if (edge.source == function.id or edge.target == function.id)
            or (edge.source.startswith(f"variable:{function.id}") or edge.target.startswith(f"variable:{function.id}"))
        ]
        related_edges = self._bounded_edges(related_edges, max_edges)
        nodes = self._dedupe_nodes([function, *self._nodes_for_edges(related_edges)])
        facts = [
            f"Callable `{self._node_label(function)}` is defined at {function.path}:{function.line}.",
            f"Outgoing resolved callees: {len(self.query.get_function_callees(symbol))}.",
            f"Incoming resolved callers: {len(self.query.get_function_callers(symbol))}.",
        ]
        return self._packet("function", self._node_label(function), nodes, related_edges, facts, len(nodes), max_edges)

    def data_flow_packet(self, source_id: str, *, max_depth: int = 6, max_edges: int = 120) -> EvidencePacket:
        trace = self.query.trace_data_from_source(source_id, max_depth=max_depth, max_edges=max_edges)
        facts = [f"Data trace from `{source_id}` selected {len(trace.edges)} edge(s)."]
        packet = self._packet("data_flow_trace", source_id, trace.nodes, trace.edges, facts, len(trace.nodes), max_edges)
        if trace.truncated:
            packet.warnings.append("Data trace was truncated by packet limits.")
        return packet

    def diagnostics_packet(self) -> EvidencePacket:
        facts: list[str] = []
        nodes: list[Node] = []
        edges: list[Edge] = []
        if self.run:
            facts.extend(
                [
                    f"Run id: {self.run.run_id}",
                    f"Status: {self.run.status}",
                    f"Skipped paths: {len(self.run.skipped_paths)}",
                    f"Parse errors: {len(self.run.parse_errors)}",
                    f"Warnings: {len(self.run.warnings)}",
                ]
            )
        unresolved = self.query.find_unresolved_calls()[:50]
        nodes.extend(unresolved)
        facts.append(f"Top unresolved call targets included in packet: {len(unresolved)}.")
        return self._packet("diagnostics", "run diagnostics", nodes, edges, facts, len(nodes), 0)

    def _packet(
        self,
        kind: str,
        subject: str,
        nodes: list[Node],
        edges: list[Edge],
        facts: list[str],
        max_nodes: int,
        max_edges: int,
    ) -> EvidencePacket:
        bounded_nodes = self._dedupe_nodes(nodes)[:max_nodes] if max_nodes else []
        bounded_edges = self._bounded_edges(edges, max_edges) if max_edges else []
        packet = EvidencePacket(
            packet_id=f"{kind}:{_safe_id(subject)}",
            kind=kind,
            subject=subject,
            instructions=self.DEFAULT_INSTRUCTIONS,
            limits={"max_nodes": max_nodes, "max_edges": max_edges},
            facts=facts,
            nodes=[node.to_dict() for node in bounded_nodes],
            edges=[edge.to_dict() for edge in bounded_edges],
        )
        packet.citations = self._citations(bounded_nodes, bounded_edges)
        if len(nodes) > len(bounded_nodes):
            packet.warnings.append(f"{len(nodes) - len(bounded_nodes)} node(s) omitted by packet node limit.")
        if len(edges) > len(bounded_edges):
            packet.warnings.append(f"{len(edges) - len(bounded_edges)} edge(s) omitted by packet edge limit.")
        return packet

    def _citations(self, nodes: list[Node], edges: list[Edge]) -> list[EvidenceCitation]:
        citations: list[EvidenceCitation] = []
        seen: set[tuple[str | None, str | None]] = set()
        for node in nodes:
            key = (node.id, None)
            if key in seen:
                continue
            seen.add(key)
            citations.append(
                EvidenceCitation(
                    citation_id=f"cite:node:{len(citations) + 1}",
                    node_id=node.id,
                    file_path=node.path,
                    line_start=node.line,
                    line_end=node.line,
                    evidence_text=self._source_line(node.path, node.line),
                )
            )
        for edge in edges:
            source = self.graph.nodes.get(edge.source)
            path = edge.properties.get("file") or (source.path if source else None)
            line = edge.properties.get("line") or (source.line if source else None)
            key = (None, edge.id)
            if key in seen:
                continue
            seen.add(key)
            citations.append(
                EvidenceCitation(
                    citation_id=f"cite:edge:{len(citations) + 1}",
                    node_id=edge.source,
                    edge_id=edge.id,
                    file_path=path,
                    line_start=line,
                    line_end=line,
                    confidence=edge.properties.get("confidence"),
                    parser=edge.properties.get("parser"),
                    reason=edge.properties.get("reason"),
                    evidence_text=self._source_line(path, line),
                )
            )
        return citations

    def _source_line(self, path: str | None, line: int | None) -> str | None:
        if not path or not line:
            return None
        source_path = Path(path)
        if not source_path.is_absolute():
            source_path = self.graph.root / source_path
        try:
            lines = source_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return None
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()[:300]
        return None

    def _nodes_for_edges(self, edges: list[Edge]) -> list[Node]:
        node_ids = {edge.source for edge in edges} | {edge.target for edge in edges}
        return [self.graph.nodes[node_id] for node_id in node_ids if node_id in self.graph.nodes]

    def _dedupe_nodes(self, nodes: list[Node]) -> list[Node]:
        seen: set[str] = set()
        output: list[Node] = []
        for node in nodes:
            if node.id in seen:
                continue
            seen.add(node.id)
            output.append(node)
        return sorted(output, key=lambda node: node.id)

    def _bounded_edges(self, edges: list[Edge], limit: int) -> list[Edge]:
        return sorted(edges, key=lambda edge: edge.id)[:limit]

    def _find_callable(self, symbol: str) -> Node | None:
        for node in [*self.graph.nodes_by_kind("function"), *self.graph.nodes_by_kind("method")]:
            if node.id == symbol or node.label == symbol or node.properties.get("qualified_name") == symbol:
                return node
        return None

    def _node_label(self, node: Node) -> str:
        return str(node.properties.get("qualified_name") or node.label)


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._:-" else "_" for ch in value)[:120]
