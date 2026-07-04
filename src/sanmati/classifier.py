from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileClassification:
    category: str
    language: str | None
    role: str
    action: str
    is_generated: bool = False
    is_vendored: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "language": self.language,
            "role": self.role,
            "action": self.action,
            "is_generated": self.is_generated,
            "is_vendored": self.is_vendored,
            "reason": self.reason,
        }


class FileClassifier:
    SOURCE_SUFFIXES = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".java": "java",
        ".c": "c",
        ".h": "c/c++",
        ".cc": "c++",
        ".cpp": "c++",
        ".hpp": "c++",
        ".go": "go",
        ".rs": "rust",
    }
    CONFIG_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env"}
    MARKUP_SUFFIXES = {".xml", ".html", ".htm", ".css"}
    DOC_SUFFIXES = {".md", ".rst", ".txt"}
    SCRIPT_SUFFIXES = {".sh", ".ps1", ".bat", ".cmd"}
    BINARY_SUFFIXES = {
        ".7z",
        ".avi",
        ".bin",
        ".bmp",
        ".class",
        ".dll",
        ".dmg",
        ".exe",
        ".gif",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp3",
        ".mp4",
        ".o",
        ".obj",
        ".pdf",
        ".png",
        ".pyc",
        ".pyd",
        ".rar",
        ".so",
        ".tar",
        ".webp",
        ".whl",
        ".zip",
    }
    GENERATED_MARKERS = {
        ".pb.py",
        "_pb2.py",
        "_pb2_grpc.py",
        ".generated.",
        ".g.py",
        ".min.js",
        ".bundle.js",
    }
    GENERATED_DIRS = {"generated", "gen", "autogen", "build"}
    VENDORED_DIRS = {"vendor", "vendored", "third_party", "external", "site-packages", "node_modules"}

    def classify(self, path: Path, *, size_bytes: int | None = None) -> FileClassification:
        name = path.name.lower()
        suffix = path.suffix.lower()
        parts = {part.lower() for part in path.parts}
        is_generated = self._is_generated(path)
        is_vendored = bool(parts & self.VENDORED_DIRS)

        if suffix in self.BINARY_SUFFIXES:
            return FileClassification("binary", None, "binary-asset", "skip", is_generated, is_vendored, "binary suffix")
        if is_vendored:
            return FileClassification("vendored", self.SOURCE_SUFFIXES.get(suffix), "vendored-dependency", "index", is_generated, True)
        if is_generated:
            return FileClassification("generated", self.SOURCE_SUFFIXES.get(suffix), "generated-source", "summarize", True, is_vendored)
        if suffix in self.SOURCE_SUFFIXES:
            return FileClassification("source", self.SOURCE_SUFFIXES[suffix], self.SOURCE_SUFFIXES[suffix], "parse")
        if suffix in self.CONFIG_SUFFIXES or name in {"package.json", "pyproject.toml"}:
            return FileClassification("config", None, "configuration", "parse")
        if name in {"dockerfile", "makefile"} or name.endswith(".mk") or suffix in {".cmake", ".bzl", ".bazel"}:
            return FileClassification("build", None, "build-system", "parse")
        if suffix in self.MARKUP_SUFFIXES:
            return FileClassification("markup", None, "markup", "parse")
        if suffix in self.SCRIPT_SUFFIXES:
            return FileClassification("script", None, "script", "parse")
        if suffix in self.DOC_SUFFIXES or name.startswith("readme"):
            return FileClassification("docs", None, "documentation", "index")
        if name.startswith("requirements") or name in {"poetry.lock", "pdm.lock", "uv.lock"}:
            return FileClassification("dependency-manifest", None, "dependency-manifest", "parse")
        return FileClassification("asset", None, "asset", "index")

    def _is_generated(self, path: Path) -> bool:
        name = path.name.lower()
        if name.startswith("gen_"):
            return True
        if any(marker in name for marker in self.GENERATED_MARKERS):
            return True
        parts = {part.lower() for part in path.parts}
        return bool(parts & self.GENERATED_DIRS)


@dataclass(frozen=True, slots=True)
class IgnoreDecision:
    skip: bool
    reason: str | None = None
    severity: str = "info"


@dataclass(slots=True)
class IgnorePolicy:
    ignored_dirs: set[str]
    ignored_suffixes: set[str]
    include_hidden: bool = False
    max_file_size: int = 1_000_000

    def directory_decision(self, path: Path) -> IgnoreDecision:
        name = path.name
        if name in self.ignored_dirs:
            return IgnoreDecision(True, "ignored_directory")
        if not self.include_hidden and name.startswith("."):
            return IgnoreDecision(True, "hidden_directory")
        return IgnoreDecision(False)

    def file_decision(self, path: Path, *, size_bytes: int | None = None) -> IgnoreDecision:
        if not self.include_hidden and any(part.startswith(".") for part in path.parts):
            return IgnoreDecision(True, "hidden_file")
        if path.suffix.lower() in self.ignored_suffixes:
            return IgnoreDecision(True, "ignored_suffix")
        if size_bytes is not None and size_bytes > self.max_file_size:
            return IgnoreDecision(True, "too_large", "warning")
        return IgnoreDecision(False)
