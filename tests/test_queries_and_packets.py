import json
from pathlib import Path

from sanmati.analyzer import analyze_project_full
from sanmati.classifier import FileClassifier, IgnorePolicy
from sanmati.exporters import export_report
from sanmati.llm_packets import EvidencePacketBuilder
from sanmati.queries import CodeQuery
from sanmati.scanner import DEFAULT_IGNORED_DIRS, DEFAULT_IGNORED_SUFFIXES
from sanmati.storage import AnalysisStore


def test_file_classifier_marks_generated_and_vendored_files() -> None:
    classifier = FileClassifier()

    generated = classifier.classify(Path("tensorflow/python/ops/gen_array_ops.py"))
    vendored = classifier.classify(Path("third_party/pkg/module.py"))
    binary = classifier.classify(Path("assets/logo.png"))

    assert generated.is_generated
    assert generated.action == "summarize"
    assert vendored.is_vendored
    assert vendored.action == "index"
    assert binary.category == "binary"
    assert binary.action == "skip"


def test_ignore_policy_reports_large_files() -> None:
    policy = IgnorePolicy(
        ignored_dirs=DEFAULT_IGNORED_DIRS,
        ignored_suffixes=DEFAULT_IGNORED_SUFFIXES,
        max_file_size=10,
    )

    decision = policy.file_decision(Path("data/large.json"), size_bytes=100)

    assert decision.skip
    assert decision.reason == "too_large"
    assert decision.severity == "warning"


def test_query_api_finds_entry_points_callees_and_unresolved_calls() -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "tiny_pipeline"
    result = analyze_project_full(root)
    query = CodeQuery(result.graph)

    entry_points = query.get_entry_points()
    callees = query.get_function_callees("src.main.run_pipeline")
    unresolved = query.find_unresolved_calls()

    assert any(node.properties.get("qualified_name") == "src.main.run_pipeline" for node in entry_points)
    assert any(node.properties.get("qualified_name") == "src.io.load_settings" for node in callees)
    assert all(node.kind == "call_target" for node in unresolved)


def test_evidence_packets_are_bounded_and_cited(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "tiny_pipeline"
    result = analyze_project_full(root)
    builder = EvidencePacketBuilder(result.graph, result.run)

    packet = builder.function_packet("src.main.run_pipeline", max_edges=10)
    out_dir = export_report(result.graph, result.summary, tmp_path / "report", result.run)
    manifest = json.loads((out_dir / "llm_packets" / "manifest.json").read_text(encoding="utf-8"))
    counts = AnalysisStore(out_dir / "analysis.sqlite").counts()

    assert packet.instructions.startswith("Use only the supplied facts")
    assert len(packet.edges) <= 10
    assert packet.citations
    assert all(citation.node_id or citation.edge_id for citation in packet.citations)
    assert manifest["principle"].startswith("LLMs explain and connect evidence")
    assert manifest["packet_count"] >= 3
    assert counts["edges"] == len(result.graph.edges)
