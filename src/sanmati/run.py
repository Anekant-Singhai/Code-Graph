from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sanmati.scanner import SkippedPath


JsonDict = dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class AnalysisEvent:
    timestamp: str
    stage: str
    message: str
    level: str = "info"
    path: str | None = None
    properties: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "timestamp": self.timestamp,
            "stage": self.stage,
            "message": self.message,
            "level": self.level,
            "path": self.path,
            "properties": self.properties,
        }


@dataclass(slots=True)
class FileResult:
    path: str
    status: str
    parser: str | None
    duration_ms: float
    nodes_added: int
    edges_added: int
    message: str | None = None

    def to_dict(self) -> JsonDict:
        return {
            "path": self.path,
            "status": self.status,
            "parser": self.parser,
            "duration_ms": round(self.duration_ms, 3),
            "nodes_added": self.nodes_added,
            "edges_added": self.edges_added,
            "message": self.message,
        }


@dataclass(slots=True)
class ParseError:
    path: str
    parser: str
    message: str
    line: int | None = None
    severity: str = "warning"

    def to_dict(self) -> JsonDict:
        return {
            "path": self.path,
            "parser": self.parser,
            "message": self.message,
            "line": self.line,
            "severity": self.severity,
        }


@dataclass(slots=True)
class AnalysisRun:
    root: str
    run_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "created"
    started_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    files_discovered: int = 0
    files_analyzed: int = 0
    graph_nodes: int = 0
    graph_edges: int = 0
    skipped_paths: list[SkippedPath] = field(default_factory=list)
    parse_errors: list[ParseError] = field(default_factory=list)
    file_results: list[FileResult] = field(default_factory=list)
    events: list[AnalysisEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def start(self) -> None:
        self.status = "running"
        self.add_event("run", "Analysis started.")

    def finish(self, *, graph_nodes: int, graph_edges: int, status: str = "completed") -> None:
        self.status = status
        self.ended_at = utc_now()
        self.graph_nodes = graph_nodes
        self.graph_edges = graph_edges
        self.files_analyzed = sum(1 for item in self.file_results if item.status in {"parsed", "indexed"})
        self.add_event("run", f"Analysis {status}.", graph_nodes=graph_nodes, graph_edges=graph_edges)

    def add_event(self, stage: str, message: str, *, level: str = "info", path: str | None = None, **properties: Any) -> None:
        self.events.append(
            AnalysisEvent(
                timestamp=utc_now(),
                stage=stage,
                message=message,
                level=level,
                path=path,
                properties={k: v for k, v in properties.items() if v is not None},
            )
        )

    def add_skipped(self, skipped: SkippedPath) -> None:
        self.skipped_paths.append(skipped)

    def add_parse_error(self, path: str, parser: str, message: str, *, line: int | None = None, severity: str = "warning") -> None:
        self.parse_errors.append(ParseError(path=path, parser=parser, message=message, line=line, severity=severity))
        self.add_event("parse", message, level=severity, path=path, parser=parser, line=line)

    def add_file_result(
        self,
        path: str,
        status: str,
        *,
        parser: str | None,
        duration_ms: float,
        nodes_added: int,
        edges_added: int,
        message: str | None = None,
    ) -> None:
        self.file_results.append(
            FileResult(
                path=path,
                status=status,
                parser=parser,
                duration_ms=duration_ms,
                nodes_added=nodes_added,
                edges_added=edges_added,
                message=message,
            )
        )

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)
        self.add_event("warning", message, level="warning")

    def to_dict(self) -> JsonDict:
        return {
            "run_id": self.run_id,
            "root": self.root,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "files_discovered": self.files_discovered,
            "files_analyzed": self.files_analyzed,
            "graph_nodes": self.graph_nodes,
            "graph_edges": self.graph_edges,
            "skipped_count": len(self.skipped_paths),
            "parse_error_count": len(self.parse_errors),
            "warning_count": len(self.warnings),
            "warnings": self.warnings,
            "skipped_paths": [item.to_dict() for item in self.skipped_paths],
            "parse_errors": [item.to_dict() for item in self.parse_errors],
            "file_results": [item.to_dict() for item in self.file_results],
            "events": [item.to_dict() for item in self.events],
        }
