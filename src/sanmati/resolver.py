from __future__ import annotations

from dataclasses import dataclass

from sanmati.models import CodeGraph, Node


@dataclass(slots=True)
class Resolution:
    node: Node
    confidence: float
    strategy: str


def resolve_call_targets(graph: CodeGraph) -> None:
    """Add resolved call edges when static names map cleanly to known functions."""
    callable_by_qname = _callable_index(graph)
    original_edges = list(graph.edges)
    for edge in original_edges:
        if edge.kind not in {"CALLS", "CALLS_UNDER_CONDITION"}:
            continue
        target = graph.nodes.get(edge.target)
        source = graph.nodes.get(edge.source)
        if target is None or source is None or target.kind != "call_target":
            continue
        resolution = _resolve_target(source, target.label, callable_by_qname)
        if resolution is None:
            continue
        resolved = resolution.node
        graph.add_edge(
            target.id,
            resolved.id,
            "RESOLVES_TO",
            confidence=resolution.confidence,
            reason=resolution.strategy,
        )
        graph.add_edge(
            source.id,
            resolved.id,
            "CALLS_RESOLVED",
            original_call=target.label,
            line=edge.properties.get("line"),
            condition=edge.properties.get("condition"),
            confidence=resolution.confidence,
            reason=resolution.strategy,
        )
        condition = str(edge.properties.get("condition", ""))
        if "__name__" in condition and "__main__" in condition:
            graph.add_edge(source.id, resolved.id, "ENTRY_POINT", reason="main_guard_call")


def _callable_index(graph: CodeGraph) -> dict[str, Node]:
    items: dict[str, Node] = {}
    for node in graph.nodes.values():
        if node.kind not in {"function", "method"}:
            continue
        qualified_name = node.properties.get("qualified_name")
        if isinstance(qualified_name, str):
            items[qualified_name] = node
    return items


def _resolve_target(source: Node, call_name: str, callable_by_qname: dict[str, Node]) -> Resolution | None:
    if call_name in callable_by_qname:
        return Resolution(callable_by_qname[call_name], 1.0, "exact qualified name")

    source_qname = source.properties.get("qualified_name")
    source_module = _source_module(source, source_qname if isinstance(source_qname, str) else None)
    if source_module and "." not in call_name:
        local_name = f"{source_module}.{call_name}"
        if local_name in callable_by_qname:
            return Resolution(callable_by_qname[local_name], 0.9, "same module local name")

    suffix_matches = [node for qname, node in callable_by_qname.items() if qname.endswith("." + call_name)]
    if len(suffix_matches) == 1:
        return Resolution(suffix_matches[0], 0.55, "unique suffix heuristic")
    return None


def _source_module(source: Node, source_qname: str | None) -> str | None:
    if source.kind == "module" and source_qname:
        return source_qname
    if source.kind == "module":
        return source.label
    if source.kind == "function" and source_qname:
        return source_qname.rsplit(".", 1)[0]
    if source.kind == "method" and source_qname:
        return source_qname.rsplit(".", 2)[0]
    return None
