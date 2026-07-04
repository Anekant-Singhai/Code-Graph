from __future__ import annotations

import argparse
from pathlib import Path

from sanmati.analyzer import analyze_project_full
from sanmati.exporters import export_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sanmati",
        description="Analyze a codebase and generate graph-backed explanation artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze a project folder.")
    analyze.add_argument("path", help="Project/folder/codebase to analyze.")
    analyze.add_argument("--out", default="sanmati-report", help="Directory where report files are written.")
    analyze.add_argument("--include-hidden", action="store_true", help="Include hidden files and directories.")
    analyze.add_argument("--max-file-size", type=int, default=1_000_000, help="Max text file size to parse in bytes.")
    analyze.add_argument("--max-files", type=int, default=None, help="Optional maximum number of files to analyze.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "analyze":
        target = Path(args.path).resolve()
        if not target.exists():
            parser.error(f"Path does not exist: {target}")
        result = analyze_project_full(
            target,
            include_hidden=args.include_hidden,
            max_file_size=args.max_file_size,
            max_files=args.max_files,
        )
        graph = result.graph
        summary = result.summary
        run = result.run
        out_dir = export_report(graph, summary, args.out, run)
        print(f"Analyzed: {target}")
        print(f"Run id: {run.run_id}")
        print(f"Files: {summary.file_count}")
        print(f"Skipped paths: {len(run.skipped_paths)}")
        print(f"Parse errors: {len(run.parse_errors)}")
        print(f"Functions/methods: {summary.function_count}")
        print(f"Classes: {summary.class_count}")
        print(f"Data kinds: {', '.join(summary.data_kinds) if summary.data_kinds else 'none'}")
        print(f"Report: {out_dir.resolve()}")
        return 0
    parser.error("Unknown command")
    return 2
