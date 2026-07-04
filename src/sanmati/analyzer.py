from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from sanmati.models import CodeGraph
from sanmati.parsers.base import AnalysisContext, ParserPlugin
from sanmati.parsers.config import SupportFileParser
from sanmati.parsers.python_ast import PythonParser
from sanmati.report import ProjectSummary, build_project_summary
from sanmati.resolver import resolve_call_targets
from sanmati.run import AnalysisRun
from sanmati.scanner import ProjectScanner, SkippedPath


@dataclass(slots=True)
class AnalysisResult:
    graph: CodeGraph
    summary: ProjectSummary
    run: AnalysisRun


def default_parsers() -> list[ParserPlugin]:
    return [PythonParser(), SupportFileParser()]


def analyze_project(
    root: str | Path,
    *,
    include_hidden: bool = False,
    max_file_size: int = 1_000_000,
    max_files: int | None = None,
) -> tuple[CodeGraph, ProjectSummary]:
    result = analyze_project_full(
        root,
        include_hidden=include_hidden,
        max_file_size=max_file_size,
        max_files=max_files,
    )
    return result.graph, result.summary


def analyze_project_full(
    root: str | Path,
    *,
    include_hidden: bool = False,
    max_file_size: int = 1_000_000,
    max_files: int | None = None,
) -> AnalysisResult:
    root_path = Path(root).resolve()
    run = AnalysisRun(root=str(root_path))
    run.start()

    scanner = ProjectScanner(root_path, include_hidden=include_hidden, max_file_size=max_file_size)
    scan_started = perf_counter()
    scan_result = scanner.scan_with_ledger()
    files = scan_result.files
    run.files_discovered = len(files)
    for skipped in scan_result.skipped:
        run.add_skipped(skipped)
    run.add_event(
        "scan",
        "Project scan completed.",
        files=len(files),
        skipped=len(scan_result.skipped),
        duration_ms=round((perf_counter() - scan_started) * 1000, 3),
    )

    if max_files is not None:
        for item in files[max_files:]:
            run.add_skipped(
                SkippedPath(
                    path=item.relative_path,
                    reason="max_files_limit",
                    severity="warning",
                    size_bytes=item.size_bytes,
                    suffix=item.suffix,
                    category=item.category,
                    action="skip",
                )
            )
        files = files[:max_files]
        run.add_warning(f"Analysis limited to first {max_files} files.")

    graph = CodeGraph(root_path)
    root_id = "project:root"
    graph.add_node(root_id, "project", root_path.name, path=root_path)

    context = AnalysisContext(files)
    parsers = default_parsers()

    for item in files:
        file_id = graph.file_node_id(item.path)
        graph.add_node(
            file_id,
            "file",
            item.relative_path,
            path=item.path,
            category=item.category,
            size_bytes=item.size_bytes,
            language=item.language,
            action=item.action,
            classification=item.classification.to_dict() if item.classification else None,
        )
        graph.add_edge(root_id, file_id, "CONTAINS", reason="project contains scanned file")
        parsed = False
        for parser in parsers:
            if parser.can_parse(item):
                parsed = True
                node_count_before = len(graph.nodes)
                edge_count_before = len(graph.edges)
                started = perf_counter()
                status = "parsed"
                message = None
                try:
                    with graph.evidence(parser=parser.name, file=item.relative_path):
                        parser.parse(item, graph, context)
                except Exception as exc:  # pragma: no cover - last-resort parser isolation.
                    status = "error"
                    message = f"{type(exc).__name__}: {exc}"
                    graph.nodes[file_id].properties["parse_error"] = message
                    run.add_parse_error(item.relative_path, parser.name, message, severity="error")
                else:
                    parse_error = graph.nodes.get(file_id).properties.get("parse_error")
                    if parse_error:
                        status = "error"
                        message = str(parse_error)
                        run.add_parse_error(item.relative_path, parser.name, message)
                run.add_file_result(
                    item.relative_path,
                    status,
                    parser=parser.name,
                    duration_ms=(perf_counter() - started) * 1000,
                    nodes_added=len(graph.nodes) - node_count_before,
                    edges_added=len(graph.edges) - edge_count_before,
                    message=message,
                )
                break
        if not parsed:
            run.add_file_result(
                item.relative_path,
                "indexed",
                parser=None,
                duration_ms=0,
                nodes_added=0,
                edges_added=1,
                message="File indexed but no deep parser is available yet.",
            )

    run.add_event("resolve", "Resolving call targets.")
    with graph.evidence(parser="resolver", confidence=0.85, reason="static call target resolution"):
        resolve_call_targets(graph)
    summary = build_project_summary(graph)
    run.finish(graph_nodes=len(graph.nodes), graph_edges=len(graph.edges))
    return AnalysisResult(graph=graph, summary=summary, run=run)
