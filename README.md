# Sanmati Codegraph

Sanmati Codegraph is a Python-first codebase intelligence framework. It scans a
project folder, builds a relationship graph from code and support files, and
exports human-readable summaries plus visualization-ready diagrams.

This is an early v1 foundation: deterministic static analysis first, with run
auditability, persistent artifacts, graph slicing, and an architecture that can
later add LLM explanation layers and language-specific parser plugins.

## What v0 Does

- Recursively scans a project while skipping common build/cache/archive folders.
- Classifies files with explicit category, language, role, action, generated,
  and vendored signals.
- Parses Python files with `ast`.
- Records files, modules, imports, classes, functions, methods, variables,
  decorators, loops, conditionals, calls, entry points, and simple data-flow
  hints.
- Parses common non-code/support files:
  JSON, TOML, INI, YAML-like files, XML, HTML, CSS, JavaScript/TypeScript
  metadata, Dockerfiles, Makefiles, shell scripts, requirements files, and env
  files.
- Detects data-kind signals such as JSON, YAML, XML, CSV, tensors, images,
  text, API responses, database rows, config values, and environment variables.
- Connects Python functions to referenced project files when paths appear in
  string literals.
- Exports:
  - `graph.json`
  - `analysis.sqlite`
  - `run.json`
  - `skipped_paths.json`
  - `parse_errors.json`
  - `visualization_manifest.json`
  - `summary.md`
  - `module_graph.mmd`
  - `call_graph.mmd`
- slice exports under `slices/`
  - LLM-ready evidence packets under `llm_packets/`

## Quick Start

From this folder:

```powershell
python -m pip install -e .
python -m sanmati analyze examples\tiny_pipeline --out work\tiny_pipeline_report
```

Without installing:

```powershell
$env:PYTHONPATH = "src"
python -m sanmati analyze examples\tiny_pipeline --out work\tiny_pipeline_report
```

## Design

The framework is centered on a universal graph model:

- `Node`: a project entity such as a file, module, function, class, config file,
  dependency, variable, or data kind.
- `Edge`: a typed relationship such as `CONTAINS`, `IMPORTS`, `CALLS`,
  `CALLS_UNDER_CONDITION`, `READS_FILE`, `USES_DATA_KIND`, `DECLARES_VARIABLE`,
  or `DEPENDS_ON_CONFIG`.
- `CodeGraph`: the in-memory graph used by parsers and exporters.

Parser plugins attach facts to the graph. Python is the first deep parser. Other
languages can be added by implementing the `ParserPlugin` protocol.

The ingestion layer now separates:

- `FileClassifier`: source/config/build/docs/generated/vendored/binary decisions.
- `IgnorePolicy`: hidden, ignored, large, and suffix-based skip decisions.
- `ProjectScanner`: recursive file discovery plus a skipped-path ledger.
- `AnalysisRun`: run state, progress events, parser errors, warnings, and
  per-file results.
- `AnalysisStore`: SQLite artifacts written by `storage.py`.

Each analysis also creates an `AnalysisRun` ledger:

- run id, status, start/end timestamps
- progress events
- per-file parser results and timings
- skipped paths with reason/severity/size
- parse errors
- graph node/edge counts

Edges include evidence metadata such as parser, file, line, confidence, and
reason wherever the framework can supply it. The Mermaid diagrams are bounded
views, not the complete graph; `visualization_manifest.json` records selected,
rendered, and omitted edge counts.

## Query And LLM Layer

`CodeQuery` exposes deterministic graph questions:

- `get_entry_points()`
- `get_module_dependencies(module)`
- `get_function_callers(symbol)`
- `get_function_callees(symbol)`
- `trace_data_from_source(source_id)`
- `trace_config_key_usage(key)`
- `find_unresolved_calls()`
- `find_high_complexity_functions()`

`EvidencePacketBuilder` prepares bounded, citation-first packets for a future
LLM layer. These packets include facts, nodes, edges, edge ids, file paths,
line numbers, confidence, parser, reason, and short source snippets. They do
not ask an LLM to parse or guess. The principle is:

> LLMs explain and connect evidence; deterministic graph facts remain the source
> of truth.

## Next Roadmap

1. Improve name resolution so `module.func()` calls resolve across files more
   accurately.
2. Add control-flow graph blocks for branches and loops.
3. Deepen intra-function and interprocedural data-flow tracing.
4. Add config-key-to-code tracing through dict subscripts and object fields.
5. Add optional LLM summarization over evidence packets, never raw guessing.
6. Add JavaScript/TypeScript parser support.
7. Add a web UI for graph exploration.
