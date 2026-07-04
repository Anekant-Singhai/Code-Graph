from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from sanmati.models import CodeGraph
from sanmati.report import ProjectSummary
from sanmati.run import AnalysisRun


class AnalysisStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write(self, graph: CodeGraph, summary: ProjectSummary, run: AnalysisRun) -> Path:
        return write_sqlite_store(graph, summary, run, self.path)

    def counts(self) -> dict[str, int]:
        with sqlite3.connect(self.path) as conn:
            return {
                "nodes": conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
                "edges": conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
                "skipped_paths": conn.execute("SELECT COUNT(*) FROM skipped_paths").fetchone()[0],
                "parse_errors": conn.execute("SELECT COUNT(*) FROM parse_errors").fetchone()[0],
                "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            }

    def find_edges(self, kind: str, *, limit: int = 100) -> list[dict[str, object]]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT graph_edge_id, source, target, kind, label, parser, file, line, confidence, reason
                FROM edges
                WHERE kind = ?
                ORDER BY edge_id
                LIMIT ?
                """,
                (kind, limit),
            ).fetchall()
        return [dict(row) for row in rows]


def write_sqlite_store(graph: CodeGraph, summary: ProjectSummary, run: AnalysisRun, path: str | Path) -> Path:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _create_schema(conn)
        _insert_run(conn, run, summary)
        _insert_nodes(conn, graph)
        _insert_edges(conn, graph)
        _insert_run_ledgers(conn, run)
        conn.commit()
    return db_path


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE run (
            run_id TEXT PRIMARY KEY,
            root TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            files_discovered INTEGER NOT NULL,
            files_analyzed INTEGER NOT NULL,
            graph_nodes INTEGER NOT NULL,
            graph_edges INTEGER NOT NULL,
            summary_json TEXT NOT NULL,
            run_json TEXT NOT NULL
        );

        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            path TEXT,
            line INTEGER,
            properties_json TEXT NOT NULL
        );

        CREATE TABLE edges (
            edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
            graph_edge_id TEXT NOT NULL,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            kind TEXT NOT NULL,
            label TEXT,
            parser TEXT,
            file TEXT,
            line INTEGER,
            confidence REAL,
            reason TEXT,
            properties_json TEXT NOT NULL
        );

        CREATE INDEX idx_nodes_kind ON nodes(kind);
        CREATE INDEX idx_edges_kind ON edges(kind);
        CREATE INDEX idx_edges_source ON edges(source);
        CREATE INDEX idx_edges_target ON edges(target);
        CREATE INDEX idx_edges_file ON edges(file);

        CREATE TABLE skipped_paths (
            path TEXT NOT NULL,
            reason TEXT NOT NULL,
            severity TEXT NOT NULL,
            size_bytes INTEGER,
            suffix TEXT,
            category TEXT,
            action TEXT NOT NULL,
            is_directory INTEGER NOT NULL
        );

        CREATE TABLE parse_errors (
            path TEXT NOT NULL,
            parser TEXT NOT NULL,
            message TEXT NOT NULL,
            line INTEGER,
            severity TEXT NOT NULL
        );

        CREATE TABLE file_results (
            path TEXT NOT NULL,
            status TEXT NOT NULL,
            parser TEXT,
            duration_ms REAL NOT NULL,
            nodes_added INTEGER NOT NULL,
            edges_added INTEGER NOT NULL,
            message TEXT
        );

        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            stage TEXT NOT NULL,
            level TEXT NOT NULL,
            path TEXT,
            message TEXT NOT NULL,
            properties_json TEXT NOT NULL
        );
        """
    )


def _insert_run(conn: sqlite3.Connection, run: AnalysisRun, summary: ProjectSummary) -> None:
    summary_json = json.dumps(asdict(summary), ensure_ascii=False)
    run_json = json.dumps(run.to_dict(), ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO run (
            run_id, root, status, started_at, ended_at, files_discovered, files_analyzed,
            graph_nodes, graph_edges, summary_json, run_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.run_id,
            run.root,
            run.status,
            run.started_at,
            run.ended_at,
            run.files_discovered,
            run.files_analyzed,
            run.graph_nodes,
            run.graph_edges,
            summary_json,
            run_json,
        ),
    )


def _insert_nodes(conn: sqlite3.Connection, graph: CodeGraph) -> None:
    conn.executemany(
        """
        INSERT INTO nodes (id, kind, label, path, line, properties_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                node.id,
                node.kind,
                node.label,
                node.path,
                node.line,
                json.dumps(node.properties, ensure_ascii=False),
            )
            for node in graph.nodes.values()
        ],
    )


def _insert_edges(conn: sqlite3.Connection, graph: CodeGraph) -> None:
    conn.executemany(
        """
        INSERT INTO edges (
            graph_edge_id, source, target, kind, label, parser, file, line, confidence, reason, properties_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                edge.id,
                edge.source,
                edge.target,
                edge.kind,
                edge.label,
                edge.properties.get("parser"),
                edge.properties.get("file"),
                edge.properties.get("line"),
                edge.properties.get("confidence"),
                edge.properties.get("reason"),
                json.dumps(edge.properties, ensure_ascii=False),
            )
            for edge in graph.edges
        ],
    )


def _insert_run_ledgers(conn: sqlite3.Connection, run: AnalysisRun) -> None:
    conn.executemany(
        """
        INSERT INTO skipped_paths (path, reason, severity, size_bytes, suffix, category, action, is_directory)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                item.path,
                item.reason,
                item.severity,
                item.size_bytes,
                item.suffix,
                item.category,
                item.action,
                1 if item.is_directory else 0,
            )
            for item in run.skipped_paths
        ],
    )
    conn.executemany(
        """
        INSERT INTO parse_errors (path, parser, message, line, severity)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(item.path, item.parser, item.message, item.line, item.severity) for item in run.parse_errors],
    )
    conn.executemany(
        """
        INSERT INTO file_results (path, status, parser, duration_ms, nodes_added, edges_added, message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                item.path,
                item.status,
                item.parser,
                item.duration_ms,
                item.nodes_added,
                item.edges_added,
                item.message,
            )
            for item in run.file_results
        ],
    )
    conn.executemany(
        """
        INSERT INTO events (timestamp, stage, level, path, message, properties_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                item.timestamp,
                item.stage,
                item.level,
                item.path,
                item.message,
                json.dumps(item.properties, ensure_ascii=False),
            )
            for item in run.events
        ],
    )
