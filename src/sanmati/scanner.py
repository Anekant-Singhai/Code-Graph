from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sanmati.classifier import FileClassification, FileClassifier, IgnorePolicy


DEFAULT_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "build",
    "dist",
    "work",
    "sanmati-report",
    "node_modules",
    ".next",
    ".turbo",
    "target",
    "coverage",
}

DEFAULT_IGNORED_SUFFIXES = {
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


@dataclass(slots=True)
class ProjectFile:
    path: Path
    relative_path: str
    suffix: str
    size_bytes: int
    category: str
    classification: FileClassification | None = None

    @property
    def language(self) -> str | None:
        return self.classification.language if self.classification else None

    @property
    def action(self) -> str:
        return self.classification.action if self.classification else "index"


@dataclass(slots=True)
class SkippedPath:
    path: str
    reason: str
    severity: str
    size_bytes: int | None
    suffix: str
    category: str | None = None
    action: str = "skip"
    is_directory: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "reason": self.reason,
            "severity": self.severity,
            "size_bytes": self.size_bytes,
            "suffix": self.suffix,
            "category": self.category,
            "action": self.action,
            "is_directory": self.is_directory,
        }


@dataclass(slots=True)
class ScanResult:
    files: list[ProjectFile]
    skipped: list[SkippedPath]


class ProjectScanner:
    def __init__(
        self,
        root: Path,
        *,
        include_hidden: bool = False,
        max_file_size: int = 1_000_000,
        ignored_dirs: set[str] | None = None,
        ignored_suffixes: set[str] | None = None,
        classifier: FileClassifier | None = None,
    ) -> None:
        self.root = root.resolve()
        self.include_hidden = include_hidden
        self.max_file_size = max_file_size
        self.ignored_dirs = ignored_dirs or DEFAULT_IGNORED_DIRS
        self.ignored_suffixes = ignored_suffixes or DEFAULT_IGNORED_SUFFIXES
        self.classifier = classifier or FileClassifier()
        self.ignore_policy = IgnorePolicy(
            ignored_dirs=self.ignored_dirs,
            ignored_suffixes=self.ignored_suffixes,
            include_hidden=self.include_hidden,
            max_file_size=self.max_file_size,
        )

    def scan(self) -> list[ProjectFile]:
        return self.scan_with_ledger().files

    def scan_with_ledger(self) -> ScanResult:
        files: list[ProjectFile] = []
        skipped: list[SkippedPath] = []
        for path in self._iter_files(self.root, skipped):
            try:
                stat = path.stat()
            except OSError:
                skipped.append(self._skipped(path, "stat_error", "warning", None))
                continue
            relative = str(path.relative_to(self.root)).replace("\\", "/")
            suffix = path.suffix.lower()
            classification = self.classifier.classify(path, size_bytes=stat.st_size)
            files.append(
                ProjectFile(
                    path=path,
                    relative_path=relative,
                    suffix=suffix,
                    size_bytes=stat.st_size,
                    category=classification.category if classification.category != "source" else classification.language or "source",
                    classification=classification,
                )
            )
        return ScanResult(
            files=sorted(files, key=lambda item: item.relative_path),
            skipped=sorted(skipped, key=lambda item: item.path),
        )

    def _iter_files(self, directory: Path, skipped: list[SkippedPath]):
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.lower())
        except OSError:
            skipped.append(self._skipped(directory, "stat_error", "warning", None, is_directory=True))
            return
        for entry in entries:
            path = Path(entry.path)
            relative_path = path.relative_to(self.root)
            try:
                if entry.is_dir(follow_symlinks=False):
                    decision = self.ignore_policy.directory_decision(relative_path)
                    if decision.skip:
                        skipped.append(self._skipped(path, decision.reason or "ignored_directory", decision.severity, 0, is_directory=True))
                        continue
                    yield from self._iter_files(path, skipped)
                elif entry.is_file(follow_symlinks=False):
                    size = self._safe_size(path)
                    classification = self.classifier.classify(path, size_bytes=size)
                    decision = self.ignore_policy.file_decision(relative_path, size_bytes=size)
                    if decision.skip or classification.action == "skip":
                        reason = decision.reason or classification.reason or "classified_skip"
                        severity = decision.severity if decision.skip else "info"
                        skipped.append(
                            self._skipped(
                                path,
                                reason,
                                severity,
                                size,
                                category=classification.category,
                                action=classification.action,
                            )
                        )
                        continue
                    yield path
            except OSError:
                skipped.append(self._skipped(path, "stat_error", "warning", None))

    def _should_skip(self, path: Path) -> bool:
        return self._skip_file_reason(path) is not None

    def _skip_directory_reason(self, path: Path) -> str | None:
        name = path.name
        if name in self.ignored_dirs:
            return "ignored_directory"
        if not self.include_hidden and name.startswith("."):
            return "hidden_directory"
        return None

    def _skip_file_reason(self, path: Path) -> str | None:
        relative = path.relative_to(self.root)
        parts = set(relative.parts)
        if parts & self.ignored_dirs:
            return "ignored_directory"
        if not self.include_hidden and any(part.startswith(".") for part in relative.parts):
            return "hidden_file"
        if path.suffix.lower() in self.ignored_suffixes:
            return "ignored_suffix"
        try:
            if path.stat().st_size > self.max_file_size:
                return "too_large"
        except OSError:
            return "stat_error"
        return None

    def _skipped(
        self,
        path: Path,
        reason: str,
        severity: str,
        size_bytes: int | None,
        *,
        category: str | None = None,
        action: str = "skip",
        is_directory: bool = False,
    ) -> SkippedPath:
        try:
            relative = str(path.relative_to(self.root)).replace("\\", "/")
        except ValueError:
            relative = str(path).replace("\\", "/")
        return SkippedPath(
            path=relative,
            reason=reason,
            severity=severity,
            size_bytes=size_bytes,
            suffix=path.suffix.lower(),
            category=category,
            action=action,
            is_directory=is_directory,
        )

    def _safe_size(self, path: Path) -> int | None:
        try:
            return path.stat().st_size
        except OSError:
            return None

    def _category_for(self, path: Path) -> str:
        name = path.name.lower()
        suffix = path.suffix.lower()
        if suffix == ".py":
            return "python"
        if suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env"}:
            return "config"
        if suffix in {".xml", ".html", ".htm", ".css"}:
            return "markup"
        if suffix in {".js", ".jsx", ".ts", ".tsx"}:
            return "frontend-code"
        if name in {"dockerfile", "makefile"} or name.endswith(".mk"):
            return "build"
        if suffix in {".sh", ".ps1", ".bat", ".cmd"}:
            return "script"
        if name.startswith("requirements") or name in {"pyproject.toml", "poetry.lock", "package.json"}:
            return "dependency-manifest"
        return "asset"
