"""Deterministic project facts and file ranking for compact handoffs."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from contextbridge.core.sanitizer import sanitize

SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".rs",
    ".go",
    ".dart",
}


def is_test(path: str) -> bool:
    name = Path(path).name.lower()
    return (
        any(part.lower() in {"test", "tests", "__tests__"} for part in Path(path).parts)
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
        or name == "conftest.py"
    )


def file_rank(path: str) -> int:
    return 2 if is_test(path) else 0 if Path(path).suffix in SOURCE_SUFFIXES else 1


def project_facts(root: Path) -> tuple[str, str]:
    goal, stack = "", []
    path = root / "pyproject.toml"
    if path.is_file() and not path.is_symlink():
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream).get("project", {})
            goal = str(data.get("description", ""))
            stack = ["Python"] + [
                re.split(r"[<>=!~\[]", str(item), maxsplit=1)[0]
                for item in data.get("dependencies", [])
            ]
            stack += [
                re.split(r"[<>=!~\[]", str(item), maxsplit=1)[0] + f" (optional {extra})"
                for extra, items in data.get("optional-dependencies", {}).items()
                if extra not in {"dev", "build"}
                for item in items
            ]
        except (OSError, ValueError):
            pass
    package = root / "package.json"
    if package.is_file() and not package.is_symlink():
        try:
            with package.open(encoding="utf-8") as stream:
                data = json.loads(stream.read(128000))
            goal = goal or str(data.get("description", ""))
            stack += ["JavaScript/TypeScript", *list(data.get("dependencies", {}))[:8]]
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    for name in ("README.md", "README.txt", "README.rst", "README"):
        path = root / name
        if not goal and path.is_file() and not path.is_symlink():
            with path.open(encoding="utf-8", errors="replace") as stream:
                text = stream.read(32000)
            paragraphs = re.split(r"\n\s*\n", text)
            goal = next(
                (
                    " ".join(p.split())
                    for p in paragraphs
                    if p.strip() and not p.lstrip().startswith(("#", ">", "```", "[", "!"))
                ),
                "",
            )
    return sanitize(goal[:1500]), sanitize(", ".join(dict.fromkeys(stack)))


def diff_line_counts(text: str) -> tuple[int, int] | None:
    counts = re.fullmatch(r"\+(\d+) / -(\d+) lines", text)
    if counts:
        return int(counts[1]), int(counts[2])
    if text.startswith(("@@", "diff ", "---")):
        lines = text.splitlines()
        return (
            sum(line.startswith("+") and not line.startswith("+++") for line in lines),
            sum(line.startswith("-") and not line.startswith("---") for line in lines),
        )
    return None


def change_size(
    snippets: list[str],
    line_delta: tuple[int, int] | None = None,
) -> tuple[int, bool]:
    """Magnitude from full live diffs or recorded summaries, independent of display limits."""
    text = snippets[0] if snippets else ""
    counts = line_delta if line_delta is not None else diff_line_counts(text)
    if counts is not None:
        added, removed = counts
        return added + removed, max(added, removed) <= 1
    byte_counts = re.search(r"(\d+) -> (\d+) bytes", text)
    if byte_counts:
        before, after = map(int, byte_counts.groups())
        return max(1, abs(after - before) // 80), False
    return 0, False


def importance(
    path: str,
    snippets: list[str],
    entrypoints: set[str] | None = None,
    *,
    line_delta: tuple[int, int] | None = None,
) -> tuple[int, int, str]:
    name = Path(path).name
    entry = name in {
        "main.py",
        "__main__.py",
        "app.py",
        "server.py",
        "main.dart",
        "index.js",
        "index.ts",
        "index.tsx",
        "main.ts",
        "main.tsx",
    }
    entry |= path in (entrypoints or set())
    core = bool(set(Path(path).parts) & {"core", "storage", "synthesis", "providers"})
    core |= name in {"service.py", "config.py"}
    magnitude = change_size(snippets, line_delta)[0]
    score = 1000 if entry else 100 if core else 50 if file_rank(path) == 0 else 0
    score += min(magnitude, 500)
    return (int(is_test(path)), -score, path)


def file_purpose(path: str, entrypoints: set[str] | None = None) -> str:
    """Short name-based descriptions, never inferred behavior from unrecorded source."""
    if path in (entrypoints or set()):
        return "declared application entry point"
    name = Path(path).stem
    known = {
        "main": "command entry point",
        "__main__": "package entry point",
        "app": "application entry point",
        "service": "shared project operations",
        "config": "configuration and defaults",
        "watcher": "file change monitoring",
        "change_logger": "change recording",
        "db": "database persistence",
        "workspace": "project storage and discovery",
        "compressor": "handoff generation",
        "context_builder": "session context assembly",
        "document": "handoff section rendering",
        "facts": "project facts and ranking",
        "chat_bridge": "conversation handoffs",
        "clipboard": "clipboard access",
        "git_observer": "Git state and diffs",
        "sanitizer": "secret redaction",
        "mainwindow": "desktop application window",
    }
    if name in known:
        return known[name]
    if name.endswith("_cmd"):
        return name.removesuffix("_cmd").replace("_", " ") + " command"
    return name.replace("_", " ").replace("-", " ") + " module (name-based description)"


def entrypoint_paths(root: Path) -> set[str]:
    """Read explicitly declared Python and JavaScript entry points."""
    paths: set[str] = set()
    python = root / "pyproject.toml"
    if python.is_file() and not python.is_symlink():
        try:
            with python.open("rb") as stream:
                project = tomllib.load(stream).get("project", {})
            for group in ("scripts", "gui-scripts"):
                for target in project.get(group, {}).values():
                    module = str(target).split(":", 1)[0].replace(".", "/") + ".py"
                    paths.update((module, "src/" + module))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    javascript = root / "package.json"
    if javascript.is_file() and not javascript.is_symlink():
        try:
            with javascript.open(encoding="utf-8") as stream:
                package = json.load(stream)
            if isinstance(package.get("main"), str):
                paths.add(package["main"].removeprefix("./"))
            binaries = package.get("bin", {})
            if isinstance(binaries, str):
                paths.add(binaries.removeprefix("./"))
            elif isinstance(binaries, dict):
                paths.update(str(value).removeprefix("./") for value in binaries.values())
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return paths
