from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]


@dataclass(slots=True)
class Node:
    id: str
    kind: str
    label: str
    path: str | None = None
    line: int | None = None
    properties: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "path": self.path,
            "line": self.line,
            "properties": self.properties,
        }


@dataclass(slots=True)
class Edge:
    source: str
    target: str
    kind: str
    id: str = ""
    label: str | None = None
    properties: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "label": self.label,
            "properties": self.properties,
        }


class CodeGraph:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._edge_index: dict[tuple[str, str, str, str | None], Edge] = {}
        self._evidence_stack: list[JsonDict] = []

    def add_node(
        self,
        node_id: str,
        kind: str,
        label: str,
        *,
        path: str | Path | None = None,
        line: int | None = None,
        **properties: Any,
    ) -> Node:
        normalized_path = self.normalize_path(path) if path is not None else None
        existing = self.nodes.get(node_id)
        if existing is not None:
            existing.properties.update({k: v for k, v in properties.items() if v is not None})
            if existing.path is None:
                existing.path = normalized_path
            if existing.line is None:
                existing.line = line
            return existing

        node = Node(
            id=node_id,
            kind=kind,
            label=label,
            path=normalized_path,
            line=line,
            properties={k: v for k, v in properties.items() if v is not None},
        )
        self.nodes[node_id] = node
        return node

    def add_edge(
        self,
        source: str,
        target: str,
        kind: str,
        *,
        label: str | None = None,
        **properties: Any,
    ) -> Edge:
        key = (source, target, kind, label)
        edge_properties = self._edge_properties(kind, label, properties)
        if key in self._edge_index:
            existing = self._edge_index[key]
            existing.properties.update(edge_properties)
            return existing
        edge_id = f"edge:{len(self.edges) + 1}"
        edge = Edge(
            source=source,
            target=target,
            kind=kind,
            id=edge_id,
            label=label,
            properties=edge_properties,
        )
        self.edges.append(edge)
        self._edge_index[key] = edge
        return edge

    @contextmanager
    def evidence(self, **properties: Any):
        self._evidence_stack.append({k: v for k, v in properties.items() if v is not None})
        try:
            yield
        finally:
            self._evidence_stack.pop()

    def _edge_properties(self, kind: str, label: str | None, properties: JsonDict) -> JsonDict:
        merged: JsonDict = {
            "parser": "core",
            "confidence": 1.0,
            "reason": label or kind.lower(),
        }
        for context in self._evidence_stack:
            merged.update(context)
        merged.update(properties)
        return {k: v for k, v in merged.items() if v is not None}

    def normalize_path(self, path: str | Path) -> str:
        path_obj = Path(path)
        try:
            return str(path_obj.resolve().relative_to(self.root)).replace("\\", "/")
        except ValueError:
            return str(path_obj).replace("\\", "/")

    def file_node_id(self, path: Path) -> str:
        return f"file:{self.normalize_path(path)}"

    def dependency_node_id(self, name: str) -> str:
        return f"dependency:{name}"

    def data_kind_node_id(self, name: str) -> str:
        return f"data_kind:{name}"

    def to_dict(self) -> JsonDict:
        return {
            "root": str(self.root),
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    def nodes_by_kind(self, kind: str) -> list[Node]:
        return [node for node in self.nodes.values() if node.kind == kind]

    def edges_by_kind(self, kind: str) -> list[Edge]:
        return [edge for edge in self.edges if edge.kind == kind]
