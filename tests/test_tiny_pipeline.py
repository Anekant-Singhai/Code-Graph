from pathlib import Path

from sanmati.analyzer import analyze_project


def test_tiny_pipeline_analysis_detects_python_and_json() -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "tiny_pipeline"
    graph, summary = analyze_project(root)

    assert summary.python_file_count == 3
    assert summary.function_count >= 5
    assert "json" in summary.data_kinds
    assert any(edge.kind == "DEPENDS_ON_CONFIG" for edge in graph.edges)
    assert any(edge.kind == "CALLS_UNDER_CONDITION" for edge in graph.edges)
    assert any(edge.kind == "CALLS_RESOLVED" for edge in graph.edges)
