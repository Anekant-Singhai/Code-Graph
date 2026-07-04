from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from sanmati.models import CodeGraph, Edge, Node


DATA_FLOW_EDGE_KINDS = {
    "DATA_FLOWS_TO",
    "PASSES_ARGUMENT",
    "ITERATES_TO",
    "RETURNS_VALUE",
    "ASSIGNS_CALL_RESULT",
    "RECEIVER_VALUE",
    "USES_DATA_KIND",
    "WRITES_TO",
}


@dataclass(slots=True)
class TraceResult:
    source_id: str
    nodes: list[Node]
    edges: list[Edge]
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "truncated": self.truncated,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }


class CodeQuery:
    def __init__(self, graph: CodeGraph) -> None:
        self.graph = graph
        self._outgoing = self._index_edges(by_source=True)
        self._incoming = self._index_edges(by_source=False)

    def get_entry_points(self) -> list[Node]:
        nodes: dict[str, Node] = {}
        for edge in self.graph.edges_by_kind("ENTRY_POINT"):
            target = self.graph.nodes.get(edge.target)
            if target is not None:
                nodes[target.id] = target
        return sorted(nodes.values(), key=lambda node: node.id)

    def get_module_dependencies(self, module: str) -> list[Node]:
        module_node = self._find_module(module)
        if module_node is None:
            return []
        deps: dict[str, Node] = {}
        for edge in self._outgoing.get(module_node.id, []):
            if edge.kind == "IMPORTS":
                target = self.graph.nodes.get(edge.target)
                if target:
                    deps[target.id] = target
        for edge in self._outgoing.get(module_node.id, []):
            if edge.kind == "CONTAINS":
                for child_edge in self._outgoing.get(edge.target, []):
                    if child_edge.kind == "IMPORTS":
                        target = self.graph.nodes.get(child_edge.target)
                        if target:
                            deps[target.id] = target
        return sorted(deps.values(), key=lambda node: node.label)

    def get_function_callers(self, symbol: str) -> list[Node]:
        target = self._find_callable(symbol)
        if target is None:
            return []
        callers: dict[str, Node] = {}
        for edge in self._incoming.get(target.id, []):
            if edge.kind == "CALLS_RESOLVED":
                source = self.graph.nodes.get(edge.source)
                if source:
                    callers[source.id] = source
        return sorted(callers.values(), key=lambda node: node.id)

    def get_function_callees(self, symbol: str) -> list[Node]:
        source = self._find_callable(symbol)
        if source is None:
            return []
        callees: dict[str, Node] = {}
        for edge in self._outgoing.get(source.id, []):
            if edge.kind == "CALLS_RESOLVED":
                target = self.graph.nodes.get(edge.target)
                if target:
                    callees[target.id] = target
        return sorted(callees.values(), key=lambda node: node.id)

    def find_unresolved_calls(self) -> list[Node]:
        resolved_sources = {edge.source for edge in self.graph.edges_by_kind("RESOLVES_TO")}
        unresolved = [
            node
            for node in self.graph.nodes_by_kind("call_target")
            if node.id not in resolved_sources and not _is_builtin_or_external_noise(node.label)
        ]
        return sorted(unresolved, key=lambda node: node.label)

    def trace_data_from_source(self, source_id: str, *, max_depth: int = 6, max_edges: int = 120) -> TraceResult:
        if source_id not in self.graph.nodes:
            return TraceResult(source_id, [], [], truncated=False)
        seen_nodes = {source_id}
        selected_edges: list[Edge] = []
        queue: deque[tuple[str, int]] = deque([(source_id, 0)])
        truncated = False

        while queue:
            node_id, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in self._outgoing.get(node_id, []):
                if edge.kind not in DATA_FLOW_EDGE_KINDS:
                    continue
                if len(selected_edges) >= max_edges:
                    truncated = True
                    break
                selected_edges.append(edge)
                if edge.target not in seen_nodes:
                    seen_nodes.add(edge.target)
                    queue.append((edge.target, depth + 1))
            if truncated:
                break

        nodes = [self.graph.nodes[node_id] for node_id in sorted(seen_nodes) if node_id in self.graph.nodes]
        return TraceResult(source_id, nodes, selected_edges, truncated)

    def trace_config_key_usage(self, key: str) -> TraceResult:
        matches = [node for node in self.graph.nodes_by_kind("config_key") if node.label.endswith(key) or node.label == key]
        if not matches:
            return TraceResult(key, [], [])
        nodes: dict[str, Node] = {node.id: node for node in matches}
        edges: list[Edge] = []
        for node in matches:
            for edge in self._incoming.get(node.id, []):
                edges.append(edge)
                source = self.graph.nodes.get(edge.source)
                if source:
                    nodes[source.id] = source
            for edge in self._outgoing.get(node.id, []):
                edges.append(edge)
                target = self.graph.nodes.get(edge.target)
                if target:
                    nodes[target.id] = target
        return TraceResult(key, sorted(nodes.values(), key=lambda item: item.id), edges)

    def find_high_complexity_functions(self, *, min_score: int = 6) -> list[tuple[Node, int]]:
        scores: list[tuple[Node, int]] = []
        for node in [*self.graph.nodes_by_kind("function"), *self.graph.nodes_by_kind("method")]:
            score = 0
            for edge in self._outgoing.get(node.id, []):
                if edge.kind in {"HAS_CONDITION", "HAS_LOOP"}:
                    score += 2
                elif edge.kind in {"CALLS", "CALLS_RESOLVED", "HAS_CALLSITE"}:
                    score += 1
            if score >= min_score:
                scores.append((node, score))
        return sorted(scores, key=lambda item: (-item[1], item[0].id))

    def _find_module(self, module: str) -> Node | None:
        for node in self.graph.nodes_by_kind("module"):
            if node.label == module or node.properties.get("qualified_name") == module:
                return node
        return None

    def _find_callable(self, symbol: str) -> Node | None:
        for node in [*self.graph.nodes_by_kind("function"), *self.graph.nodes_by_kind("method")]:
            if node.id == symbol or node.label == symbol or node.properties.get("qualified_name") == symbol:
                return node
        return None

    def _index_edges(self, *, by_source: bool) -> dict[str, list[Edge]]:
        index: dict[str, list[Edge]] = defaultdict(list)
        for edge in self.graph.edges:
            index[edge.source if by_source else edge.target].append(edge)
        return index


def _is_builtin_or_external_noise(label: str) -> bool:
    builtins = {
        "any",
        "bool",
        "dict",
        "float",
        "int",
        "isinstance",
        "len",
        "list",
        "max",
        "min",
        "print",
        "range",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "type",
    }
    return label in builtins or label.startswith(("self.", "super."))
