"""Small, local-only framework conventions and TypeScript import settings."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any


def read_json_config(path: Path, root: Path, seen: set[Path] | None = None) -> dict[str, Any]:
    """Bounded JSONC, including local extends; never follow configs outside the project."""
    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen or len(seen) >= 6 or not path.is_relative_to(root.resolve()):
        return {}
    seen.add(path)
    try:
        with path.open() as stream:
            text = stream.read(65537)
        if len(text) > 65536:
            return {}
        text = re.sub(
            r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',
            lambda m: m[0] if m[0].startswith('"') else " ",
            text,
            flags=re.S,
        )
        text = re.sub(
            r'"(?:\\.|[^"\\])*"|,\s*(?=[}\]])', lambda m: m[0] if m[0].startswith('"') else "", text
        )
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        base: dict[str, Any] = {}
        if isinstance(data.get("extends"), str) and data["extends"].startswith("."):
            parent = path.parent / data["extends"]
            if not parent.suffix:
                parent = parent.with_suffix(".json")
            base = read_json_config(parent, root, seen)
        options = dict(base.get("compilerOptions", {}))
        current = data.get("compilerOptions", {})
        if isinstance(current, dict):
            if "baseUrl" in current:
                options["baseUrl"] = str((path.parent / current["baseUrl"]).resolve())
            if isinstance(current.get("paths"), dict):
                options["paths"] = current["paths"]
                options["pathsBase"] = options.get("baseUrl", str(path.parent))
        return {**base, **data, "compilerOptions": options}
    except (OSError, ValueError, TypeError):
        return {}


def import_targets(root: Path, uri: str, options: dict[str, Any]) -> list[str]:
    result: list[str] = []
    paths = options.get("paths", {})
    if isinstance(paths, dict):
        for alias, targets in sorted(paths.items(), key=lambda pair: -len(pair[0])):
            prefix, star, suffix = alias.partition("*")
            if (not star and uri != alias) or (
                star and not (uri.startswith(prefix) and uri.endswith(suffix))
            ):
                continue
            middle = uri[len(prefix) : len(uri) - len(suffix) if suffix else None] if star else ""
            for target in targets if isinstance(targets, list) else []:
                if isinstance(target, str):
                    full = (
                        Path(options.get("pathsBase", root)) / target.replace("*", middle)
                    ).resolve()
                    if full.is_relative_to(root.resolve()):
                        result.append(full.relative_to(root.resolve()).as_posix())
            return result
    if options.get("baseUrl"):
        full = (Path(options["baseUrl"]) / uri).resolve()
        if full.is_relative_to(root.resolve()):
            result.append(full.relative_to(root.resolve()).as_posix())
    return result


def framework_label(path: str) -> str | None:
    if path == "lib/main.dart":
        return "Flutter app startup (lib/main.dart)"
    parts = Path(path).parts
    if "app" not in parts or Path(path).stem not in {"page", "layout", "route"}:
        return None
    if Path(path).suffix not in {".tsx", ".jsx", ".ts", ".js"}:
        return None
    folders = parts[parts.index("app") + 1 : -1]
    folders = tuple(p for p in folders if not p.startswith(("(", "@")))
    route = "/" + "/".join(folders)
    role = Path(path).stem
    title = "Home" if not folders else folders[-1].replace("-", " ").capitalize()
    if role == "layout":
        title = "Root" if not folders else title
    return f"{title} {role} ({route})" if role != "route" else f"API route ({route})"


def source_routes(path: str, source: str) -> list[str]:
    routes: list[str] = []
    verbs = {"get", "post", "put", "patch", "delete", "route"}
    if path.endswith(".py") and re.search(r"(?:from|import)\s+flask\b", source):
        try:
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for d in node.decorator_list:
                    if (
                        isinstance(d, ast.Call)
                        and isinstance(d.func, ast.Attribute)
                        and d.func.attr in verbs
                        and d.args
                        and isinstance(d.args[0], ast.Constant)
                        and isinstance(d.args[0].value, str)
                    ):
                        routes.append(f"{d.func.attr.upper()} {d.args[0].value}")
        except (SyntaxError, ValueError, RecursionError):
            pass
    else:
        clean = re.sub(
            r"/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`",
            lambda m: " " if m[0].startswith(("//", "/*")) else m[0],
            source,
            flags=re.S,
        )
        if not re.search(r"(?:from\s*|require\(\s*)['\"]express['\"]", clean):
            return []
        quoted = list(
            re.finditer(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`", clean, re.S)
        )
        routes = [
            f"{m[1].upper()} {m[2]}"
            for m in re.finditer(
                r"\b(?:app|router)\.(get|post|put|patch|delete|all)\(\s*['\"]([^'\"]+)['\"]", clean
            )
            if not any(q.start() < m.start() < q.end() for q in quoted)
        ]
    return list(dict.fromkeys(routes))[:20]
