import json
import sqlite3
from pathlib import Path

from sanmati.analyzer import analyze_project_full
from sanmati.exporters import export_report, render_module_mermaid
from sanmati.models import CodeGraph


def test_run_ledgers_parse_errors_skips_and_sqlite_export(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "good.py").write_text(
        "import json\n"
        "\n"
        "def load_config():\n"
        "    with open('config.json', 'r', encoding='utf-8') as handle:\n"
        "        return json.load(handle)\n",
        encoding="utf-8",
    )
    (project / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (project / "config.json").write_text('{"input": {"path": "events.json"}}', encoding="utf-8")
    (project / "large.txt").write_text("x" * 200, encoding="utf-8")
    (project / ".hidden.py").write_text("def hidden(): pass\n", encoding="utf-8")

    result = analyze_project_full(project, max_file_size=80)
    out_dir = export_report(result.graph, result.summary, tmp_path / "report", result.run)

    assert result.run.status == "completed"
    assert any(item.reason == "too_large" for item in result.run.skipped_paths)
    assert any(item.reason == "hidden_file" for item in result.run.skipped_paths)
    assert any(item.path == "bad.py" for item in result.run.parse_errors)
    assert (out_dir / "analysis.sqlite").exists()
    assert (out_dir / "run.json").exists()
    assert (out_dir / "skipped_paths.json").exists()
    assert (out_dir / "parse_errors.json").exists()

    manifest = json.loads((out_dir / "visualization_manifest.json").read_text(encoding="utf-8"))
    assert manifest["visualizations"]

    with sqlite3.connect(out_dir / "analysis.sqlite") as conn:
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        skipped_count = conn.execute("SELECT COUNT(*) FROM skipped_paths").fetchone()[0]
        error_count = conn.execute("SELECT COUNT(*) FROM parse_errors").fetchone()[0]
    assert node_count == len(result.graph.nodes)
    assert skipped_count == len(result.run.skipped_paths)
    assert error_count == len(result.run.parse_errors)


def test_every_edge_has_basic_evidence_metadata() -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "tiny_pipeline"
    result = analyze_project_full(root)

    assert result.graph.edges
    for edge in result.graph.edges:
        assert "parser" in edge.properties
        assert "confidence" in edge.properties
        assert "reason" in edge.properties


def test_suffix_constants_do_not_create_data_kind_false_positives(tmp_path: Path) -> None:
    project = tmp_path / "suffix_project"
    project.mkdir()
    (project / "constants.py").write_text(
        "def suffixes():\n"
        "    values = ['.json', '.yaml', '.png']\n"
        "    return values\n",
        encoding="utf-8",
    )

    result = analyze_project_full(project)

    assert result.summary.data_kinds == []


def test_tiny_pipeline_has_lightweight_data_flow_edges() -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "tiny_pipeline"
    result = analyze_project_full(root)
    edge_kinds = {edge.kind for edge in result.graph.edges}

    assert "HAS_PARAMETER" in edge_kinds
    assert "PASSES_ARGUMENT" in edge_kinds
    assert "DATA_FLOWS_TO" in edge_kinds
    assert "ITERATES_TO" in edge_kinds
    assert "RETURNS_VALUE" in edge_kinds


def test_visualization_truncation_is_reported() -> None:
    graph = CodeGraph(Path.cwd())
    graph.add_node("project:root", "project", "root")
    previous = "project:root"
    for index in range(260):
        node_id = f"file:file_{index}.py"
        graph.add_node(node_id, "file", f"file_{index}.py")
        graph.add_edge(previous, node_id, "CONTAINS")
        previous = node_id

    mermaid, meta = render_module_mermaid(graph)

    assert meta.omitted_edges > 0
    assert "omitted" in mermaid
