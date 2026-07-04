from __future__ import annotations

import configparser
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from sanmati.models import CodeGraph
from sanmati.parsers.base import AnalysisContext
from sanmati.scanner import ProjectFile


class SupportFileParser:
    name = "support-files"

    SUPPORTED_SUFFIXES = {
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".env",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".sh",
        ".ps1",
        ".bat",
        ".cmd",
        ".txt",
        ".md",
    }

    def can_parse(self, project_file: ProjectFile) -> bool:
        name = project_file.path.name.lower()
        return (
            project_file.suffix in self.SUPPORTED_SUFFIXES
            or name in {"dockerfile", "makefile"}
            or name.endswith(".mk")
            or name.startswith("requirements")
        )

    def parse(self, project_file: ProjectFile, graph: CodeGraph, context: AnalysisContext) -> None:
        file_id = graph.file_node_id(project_file.path)
        graph.add_node(
            file_id,
            "file",
            project_file.relative_path,
            path=project_file.path,
            category=project_file.category,
            size_bytes=project_file.size_bytes,
        )

        suffix = project_file.suffix
        name = project_file.path.name.lower()
        text = _read_text(project_file.path)
        if text is None:
            graph.nodes[file_id].properties["parse_error"] = "Could not decode as text."
            return

        if suffix == ".json" or name == "package.json":
            self._parse_json(text, file_id, graph)
        elif suffix == ".toml":
            self._parse_toml(project_file.path, file_id, graph)
        elif suffix in {".ini", ".cfg"}:
            self._parse_ini(text, file_id, graph)
        elif suffix in {".yaml", ".yml"}:
            self._parse_yaml_like(text, file_id, graph)
        elif suffix == ".env":
            self._parse_env(text, file_id, graph)
        elif suffix == ".xml":
            self._parse_xml(text, file_id, graph)
        elif suffix in {".html", ".htm"}:
            self._parse_html(text, file_id, graph)
        elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
            self._parse_js_like(text, file_id, graph)
        elif name.startswith("requirements"):
            self._parse_requirements(text, file_id, graph)
        elif name == "dockerfile":
            self._parse_dockerfile(text, file_id, graph)
        elif name == "makefile" or name.endswith(".mk"):
            self._parse_makefile(text, file_id, graph)
        else:
            graph.nodes[file_id].properties["line_count"] = text.count("\n") + 1

        self._tag_data_kind(project_file, file_id, graph)

    def _parse_json(self, text: str, file_id: str, graph: CodeGraph) -> None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            graph.nodes[file_id].properties["parse_error"] = f"JSON parse error: {exc}"
            return
        graph.nodes[file_id].properties.update(_shape_summary(data))
        for key_path, value_kind in _walk_shape(data):
            node_id = f"config_key:{file_id}:{key_path}"
            graph.add_node(node_id, "config_key", key_path, value_kind=value_kind)
            graph.add_edge(file_id, node_id, "DECLARES_CONFIG_KEY")

    def _parse_toml(self, path: Path, file_id: str, graph: CodeGraph) -> None:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            graph.nodes[file_id].properties["parse_error"] = f"TOML parse error: {exc}"
            return
        graph.nodes[file_id].properties.update(_shape_summary(data))
        for key_path, value_kind in _walk_shape(data):
            node_id = f"config_key:{file_id}:{key_path}"
            graph.add_node(node_id, "config_key", key_path, value_kind=value_kind)
            graph.add_edge(file_id, node_id, "DECLARES_CONFIG_KEY")

    def _parse_ini(self, text: str, file_id: str, graph: CodeGraph) -> None:
        parser = configparser.ConfigParser()
        try:
            parser.read_string(text)
        except configparser.Error as exc:
            graph.nodes[file_id].properties["parse_error"] = f"INI parse error: {exc}"
            return
        sections = parser.sections()
        graph.nodes[file_id].properties["sections"] = sections
        for section in sections:
            for key in parser[section]:
                key_path = f"{section}.{key}"
                node_id = f"config_key:{file_id}:{key_path}"
                graph.add_node(node_id, "config_key", key_path, value_kind="string")
                graph.add_edge(file_id, node_id, "DECLARES_CONFIG_KEY")

    def _parse_yaml_like(self, text: str, file_id: str, graph: CodeGraph) -> None:
        keys: list[str] = []
        for line in text.splitlines():
            match = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*:", line)
            if match:
                keys.append(match.group(1))
        graph.nodes[file_id].properties["yaml_keys_detected"] = keys[:100]
        for key in keys[:100]:
            node_id = f"config_key:{file_id}:{key}"
            graph.add_node(node_id, "config_key", key, value_kind="unknown")
            graph.add_edge(file_id, node_id, "DECLARES_CONFIG_KEY")

    def _parse_env(self, text: str, file_id: str, graph: CodeGraph) -> None:
        keys: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            keys.append(stripped.split("=", 1)[0].strip())
        graph.nodes[file_id].properties["env_keys"] = keys[:100]
        for key in keys[:100]:
            node_id = f"env_var:{key}"
            graph.add_node(node_id, "environment_variable", key)
            graph.add_edge(file_id, node_id, "DECLARES_ENV_VAR")

    def _parse_xml(self, text: str, file_id: str, graph: CodeGraph) -> None:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            graph.nodes[file_id].properties["parse_error"] = f"XML parse error: {exc}"
            return
        tags: dict[str, int] = {}
        for element in root.iter():
            tags[element.tag] = tags.get(element.tag, 0) + 1
        graph.nodes[file_id].properties["root_tag"] = root.tag
        graph.nodes[file_id].properties["tags"] = tags

    def _parse_html(self, text: str, file_id: str, graph: CodeGraph) -> None:
        scripts = re.findall(r"<script[^>]*src=[\"']([^\"']+)[\"']", text, flags=re.IGNORECASE)
        styles = re.findall(r"<link[^>]*href=[\"']([^\"']+)[\"']", text, flags=re.IGNORECASE)
        graph.nodes[file_id].properties["script_refs"] = scripts
        graph.nodes[file_id].properties["style_refs"] = styles

    def _parse_js_like(self, text: str, file_id: str, graph: CodeGraph) -> None:
        imports = re.findall(r"(?:import\s+.*?\s+from\s+|require\()[\"']([^\"']+)[\"']", text)
        graph.nodes[file_id].properties["imports"] = imports
        for item in imports:
            dep_id = graph.dependency_node_id(item)
            graph.add_node(dep_id, "dependency", item, ecosystem="javascript")
            graph.add_edge(file_id, dep_id, "IMPORTS")

    def _parse_requirements(self, text: str, file_id: str, graph: CodeGraph) -> None:
        deps: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            name = re.split(r"[<>=~!;\[]", stripped, maxsplit=1)[0].strip()
            if name:
                deps.append(name)
                dep_id = graph.dependency_node_id(name)
                graph.add_node(dep_id, "dependency", name, ecosystem="python")
                graph.add_edge(file_id, dep_id, "DECLARES_DEPENDENCY")
        graph.nodes[file_id].properties["dependencies"] = deps

    def _parse_dockerfile(self, text: str, file_id: str, graph: CodeGraph) -> None:
        instructions: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            instructions.append(stripped.split(maxsplit=1)[0].upper())
        graph.nodes[file_id].properties["instructions"] = instructions

    def _parse_makefile(self, text: str, file_id: str, graph: CodeGraph) -> None:
        targets = re.findall(r"^([A-Za-z0-9_.-]+)\s*:", text, flags=re.MULTILINE)
        graph.nodes[file_id].properties["targets"] = targets

    def _tag_data_kind(self, project_file: ProjectFile, file_id: str, graph: CodeGraph) -> None:
        suffix_to_kind = {
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml-config",
            ".ini": "ini-config",
            ".cfg": "ini-config",
            ".env": "environment-config",
            ".xml": "xml",
            ".html": "html",
            ".htm": "html",
            ".css": "css",
            ".csv": "csv",
            ".txt": "text",
            ".md": "markdown",
        }
        data_kind = suffix_to_kind.get(project_file.suffix)
        if data_kind:
            kind_id = graph.data_kind_node_id(data_kind)
            graph.add_node(kind_id, "data_kind", data_kind)
            graph.add_edge(file_id, kind_id, "REPRESENTS_DATA_KIND")


def _read_text(path: Path) -> str | None:
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    return None


def _shape_summary(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return {"shape": "object", "top_level_keys": list(data.keys())[:100]}
    if isinstance(data, list):
        item_types = sorted({type(item).__name__ for item in data[:100]})
        return {"shape": "array", "length": len(data), "item_types": item_types}
    return {"shape": type(data).__name__}


def _walk_shape(data: Any, prefix: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            key_path = f"{prefix}.{key}" if prefix else str(key)
            found.append((key_path, _value_kind(value)))
            found.extend(_walk_shape(value, key_path))
    elif isinstance(data, list):
        for index, value in enumerate(data[:10]):
            key_path = f"{prefix}[{index}]"
            found.append((key_path, _value_kind(value)))
            found.extend(_walk_shape(value, key_path))
    return found


def _value_kind(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__
