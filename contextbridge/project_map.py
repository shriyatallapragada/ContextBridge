"""Bounded static Python import map. Never imports or executes project code."""

from __future__ import annotations

import ast
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SKIP = {"node_modules", "dist", "build", "vendor", "__pycache__", "tests", "test"}


@dataclass(frozen=True)
class ProjectMap:
    nodes: dict[str, list[str]]
    edges: list[tuple[str, str]]
    scanned: int
    skipped: int
    limited: bool


def scan_project(root: Path) -> ProjectMap:
    modules: dict[str, tuple[str, str, ast.Module]] = {}
    skipped = total = walked = 0
    limited = False
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".") and d not in SKIP and not (Path(directory) / d).is_symlink()
        )
        walked += 1
        if walked > 200:
            limited = True
            break
        for name in sorted(files):
            path = Path(directory) / name
            if not name.endswith(".py") or path.is_symlink():
                continue
            if len(modules) >= 180 or total >= 2_000_000:
                limited = True
                break
            try:
                with path.open("rb") as stream:
                    source = stream.read(65537)
                total += len(source)
                if len(source) > 65536:
                    skipped += 1
                    continue
                tree = ast.parse(source)
            except (OSError, SyntaxError, ValueError, RecursionError):
                skipped += 1
                continue
            relative = path.relative_to(root)
            parts = list(relative.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            module = ".".join(parts)
            group = "/".join(relative.parts[:2]) if len(relative.parts) > 2 else str(relative)
            modules[module] = (group, relative.as_posix(), tree)
        if limited:
            break
    nodes: dict[str, list[str]] = {}
    edges: set[tuple[str, str]] = set()
    for module, (group, source_path, tree) in modules.items():
        nodes.setdefault(group, []).append(source_path)
        package = (
            module.split(".") if source_path.endswith("__init__.py") else module.split(".")[:-1]
        )
        for item in ast.walk(tree):
            targets: list[str] = []
            if isinstance(item, ast.Import):
                targets = [a.name for a in item.names]
            elif isinstance(item, ast.ImportFrom):
                prefix = package[: len(package) - item.level + 1] if item.level else []
                base = ".".join([*prefix, *([item.module] if item.module else [])])
                targets = [base, *(f"{base}.{a.name}".strip(".") for a in item.names)]
            for target in targets:
                if target in modules and modules[target][0] != group:
                    edges.add((group, modules[target][0]))
    return ProjectMap(nodes, sorted(edges), len(modules), skipped, limited)


def visible_map(model: ProjectMap, limit: int = 12) -> ProjectMap:
    counts = Counter(n for edge in model.edges for n in edge)
    selected = sorted(model.nodes, key=lambda n: (-counts[n], n))[:limit]
    return ProjectMap(
        {n: model.nodes[n] for n in selected},
        [(a, b) for a, b in model.edges if a in selected and b in selected],
        model.scanned,
        model.skipped,
        model.limited or len(selected) < len(model.nodes),
    )
