from __future__ import annotations

from typing import Protocol

from sanmati.models import CodeGraph
from sanmati.scanner import ProjectFile


class ParserPlugin(Protocol):
    name: str

    def can_parse(self, project_file: ProjectFile) -> bool:
        ...

    def parse(self, project_file: ProjectFile, graph: CodeGraph, context: "AnalysisContext") -> None:
        ...


class AnalysisContext:
    def __init__(self, files: list[ProjectFile]) -> None:
        self.files = files
        self.path_index = {item.relative_path: item for item in files}
        self.basename_index: dict[str, list[ProjectFile]] = {}
        for item in files:
            self.basename_index.setdefault(item.path.name, []).append(item)

    def match_string_to_file(self, value: str) -> ProjectFile | None:
        normalized = value.replace("\\", "/").lstrip("./")
        if normalized in self.path_index:
            return self.path_index[normalized]
        for relative, item in self.path_index.items():
            if relative.endswith("/" + normalized):
                return item
        matches = self.basename_index.get(normalized)
        if matches and len(matches) == 1:
            return matches[0]
        return None
