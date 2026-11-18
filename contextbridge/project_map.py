"""Bounded static Python import map. Never imports or executes project code."""

from __future__ import annotations

import ast
import hashlib
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from contextbridge.core.sanitizer import sanitize
from contextbridge.core.watcher import excluded, ignored_by_gitignore
from contextbridge.core.web_facts import (
    framework_label,
    import_targets,
    read_json_config,
    source_routes,
)
from contextbridge.errors import UserError
from contextbridge.synthesis.facts import entrypoint_paths, importance

SKIP = {
    "node_modules",
    "dist",
    "build",
    "vendor",
    "__pycache__",
    "tests",
    "test",
    "secrets",
    "credentials",
    "keys",
}


@dataclass
class ProjectMap:
    nodes: dict[str, list[str]]
    edges: list[tuple[str, str]]
    scanned: int
    skipped: int
    limited: bool
    graph_nodes: dict[str, GraphNode] = field(default_factory=dict)
    graph_edges: list[GraphEdge] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)
    entrypoints: list[str] = field(default_factory=list)
    unresolved_calls: int = 0
    detected_languages: list[str] = field(default_factory=list)
    component_summaries: list[dict[str, Any]] = field(default_factory=list)


def scan_project(
    root: Path,
    *,
    cached: list[dict[str, Any]] | None = None,
    explanations: list[dict[str, str]] | None = None,
    ignore_globs: list[str] | None = None,
    components: list[dict[str, Any]] | None = None,
) -> ProjectMap:
    modules: dict[str, tuple[str, str, ast.Module]] = {}
    records: dict[str, tuple[str, ast.Module | None]] = {}
    hashes: dict[str, str] = {}
    skipped = total = walked = 0
    limited = False
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d not in SKIP
            and not (Path(directory) / d).is_symlink()
            and not excluded((Path(directory) / d).relative_to(root).as_posix(), ignore_globs or [])
            and not ignored_by_gitignore(
                root, (Path(directory) / d).relative_to(root).as_posix() + "/"
            )
        )
        walked += 1
        if walked > 200:
            limited = True
            break
        for name in sorted(files):
            path = Path(directory) / name
            relative_path = path.relative_to(root).as_posix()
            if (
                path.suffix
                not in {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".dart"}
                or path.is_symlink()
                or path.stem.lower() in {"secrets", "credentials", "keys", "private_key"}
                or excluded(relative_path, ignore_globs or [])
                or ignored_by_gitignore(root, relative_path)
            ):
                continue
            if len(records) >= 180 or total >= 2_000_000:
                limited = True
                break
            try:
                with path.open("rb") as stream:
                    source = stream.read(65537)
                total += len(source)
                if len(source) > 65536:
                    skipped += 1
                    continue
                text = source.decode("utf-8", errors="replace")
                tree = ast.parse(text) if name.endswith(".py") else None
                records[relative_path] = (text, tree)
                hashes[relative_path] = hashlib.sha256(source).hexdigest()
            except (OSError, SyntaxError, ValueError, RecursionError):
                skipped += 1
                continue
            if tree is None:
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
    model = ProjectMap(nodes, sorted(edges), len(records), skipped, limited, hashes=hashes)
    _populate_graph(model, records, cached or [], explanations or [], entrypoint_paths(root))
    model.component_summaries = components or []
    _language_imports(root, model, records)
    return model


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


@dataclass
class GraphNode:
    id: str
    kind: str
    label: str
    path: str
    group: str
    purpose: str
    ai_explanation: dict[str, str] | None = None
    related_files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    used_by: list[str] = field(default_factory=list)
    line: int | None = None
    file_count: int = 1
    purpose_source: str = "offline"
    summary_cached: bool = False
    routes: list[str] = field(default_factory=list)
    explained_files: int = 0


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    kind: str
    evidence: str = ""
    count: int = 1


def node_id(kind: str, path: str, symbol: str = "") -> str:
    return "n_" + hashlib.sha256(f"{kind}\0{path}\0{symbol}".encode()).hexdigest()[:20]


def _module(path: str) -> str:
    value = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    return value.removesuffix(".__init__")


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return prefix + "." + node.attr if prefix else ""
    return ""


def _populate_graph(
    model: ProjectMap,
    records: dict[str, tuple[str, ast.Module | None]],
    cached: list[dict[str, Any]],
    explanations: list[dict[str, str]],
    entries: set[str],
) -> None:
    from contextbridge.synthesis.project_summary import static_file

    modules = {_module(p): p for p, (_, tree) in records.items() if tree is not None}
    definitions: dict[tuple[str, str], GraphNode] = {}
    syntax: dict[str, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = {}
    bindings: dict[str, dict[str, tuple[str, str]]] = {}
    file_nodes: dict[str, GraphNode] = {}
    for path, (source, tree) in records.items():
        digest = model.hashes[path]
        matches = [
            r
            for r in cached
            if r["path"] == path and r["content_hash"] == digest and "purpose" in r
        ]
        fact = next(
            (r for r in matches if r.get("method") == "model"), matches[0] if matches else None
        )
        purpose = fact["purpose"] if fact else static_file(path, source, digest, entries).purpose
        if not fact:
            if tree is not None:
                purpose = ast.get_docstring(tree) or "Not explained yet"
            else:
                doc = re.search(r"(?m)^\s*///\s*(.+)|/\*\*\s*([^*]+)", source)
                purpose = (doc[1] or doc[2]).strip() if doc else "Not explained yet"
        if purpose.startswith(("Defines ", "Declares ", "Contains ", "Imports ")):
            purpose = "Not explained yet"
        ai = next(
            (
                r["explanation"]
                for r in explanations
                if r["path"] == path and r["content_hash"] == digest
            ),
            "",
        )
        kind = "module" if tree is not None else "file"
        file_node = GraphNode(
            node_id(kind, path),
            kind,
            framework_label(path) or Path(path).name,
            path,
            Path(path).parent.as_posix(),
            sanitize(purpose),
            {"text": sanitize(ai), "label": "AI-written"} if ai else None,
            [path],
            purpose_source="AI-written" if fact and fact.get("method") == "model" else "offline",
            summary_cached=bool(fact),
            routes=source_routes(path, source),
        )
        model.graph_nodes[file_node.id] = file_node
        file_nodes[path] = file_node
        if file_node.routes and not framework_label(path):
            file_node.label += " — " + "; ".join(file_node.routes[:2])
        if tree is None:
            continue

        class Definitions(ast.NodeVisitor):
            def __init__(self, path: str, group: str) -> None:
                self.path, self.group = path, group
                self.scope: list[str] = []
                self.count = 0

            def visit_definition(
                self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ) -> None:
                if self.count >= 80 or len(model.graph_nodes) >= 4000:
                    model.limited = True
                    return
                self.count += 1
                qualified = ".".join([*self.scope, node.name])
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                doc = ast.get_docstring(node)
                purpose = " ".join(doc.splitlines()[:1]) if doc else "Not explained yet"
                item = GraphNode(
                    node_id(kind, self.path, qualified),
                    kind,
                    qualified,
                    self.path,
                    self.group,
                    sanitize(purpose),
                    related_files=[self.path],
                    line=node.lineno,
                )
                model.graph_nodes[item.id] = item
                definitions[self.path, qualified] = item
                syntax[item.id] = node
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            visit_FunctionDef = visit_definition
            visit_AsyncFunctionDef = visit_definition
            visit_ClassDef = visit_definition

        Definitions(path, file_node.group).visit(tree)
        local: dict[str, tuple[str, str]] = {}
        package = (
            _module(path).split(".")
            if path.endswith("__init__.py")
            else _module(path).split(".")[:-1]
        )
        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                for alias in item.names:
                    target = alias.name if alias.asname else alias.name.split(".")[0]
                    local[alias.asname or target] = (modules.get(target, ""), "")
                    if alias.name in modules:
                        model.graph_edges.append(
                            GraphEdge(
                                file_node.id,
                                file_nodes[modules[alias.name]].id
                                if modules[alias.name] in file_nodes
                                else node_id("module", modules[alias.name]),
                                "imports",
                                f"{path}:{item.lineno}",
                            )
                        )
            elif isinstance(item, ast.ImportFrom):
                prefix = package[: len(package) - item.level + 1] if item.level else []
                base = ".".join([*prefix, *([item.module] if item.module else [])])
                for alias in item.names:
                    full = (base + "." + alias.name).strip(".")
                    target, symbol = (full, "") if full in modules else (base, alias.name)
                    if target in modules:
                        local[alias.asname or alias.name] = (modules[target], symbol)
                        model.graph_edges.append(
                            GraphEdge(
                                file_node.id,
                                node_id("module", modules[target]),
                                "imports",
                                f"{path}:{item.lineno}",
                            )
                        )
        bindings[path] = local

    seen_edges: set[tuple[str, str, str]] = set()
    class_types: dict[tuple[str, str, str], GraphNode] = {}

    def resolve(
        path: str, text: str, scope: str, cls: str, env: dict[str, GraphNode]
    ) -> GraphNode | None:
        parts = text.split(".")
        if not text:
            return None
        for length in range(len(parts), 0, -1):
            prefix = ".".join(parts[:length])
            target_type = env.get(prefix)
            if prefix.startswith("self.") or prefix.startswith("cls."):
                target_type = target_type or class_types.get((path, cls, prefix))
            if target_type:
                target_name = target_type.label + (
                    "." + ".".join(parts[length:]) if length < len(parts) else ""
                )
                return definitions.get(
                    (target_type.path, target_name), target_type if length == len(parts) else None
                )
        if parts[0] in {"self", "cls"} and cls:
            return definitions.get((path, cls + "." + ".".join(parts[1:])))
        if parts[0] in bindings.get(path, {}):
            target, symbol = bindings[path][parts[0]]
            qualified = ".".join(p for p in [symbol, *parts[1:]] if p)
            found = definitions.get((target, qualified))
            if found:
                return found
        for length in range(len(parts), 0, -1):
            module = ".".join(parts[:length])
            if module in modules:
                return definitions.get((modules[module], ".".join(parts[length:])))
        scopes = scope.split(".") if scope else []
        while True:
            found = definitions.get((path, ".".join([*scopes, text])))
            if found:
                return found
            if not scopes:
                return None
            scopes.pop()

    def inferred(
        path: str, value: ast.AST, scope: str, cls: str, env: dict[str, GraphNode]
    ) -> GraphNode | None:
        if isinstance(value, ast.Call):
            target = resolve(path, _dotted(value.func), scope, cls, env)
            if target and target.kind == "class":
                return target
            definition = syntax.get(target.id) if target else None
            if (
                isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
                and definition.returns
            ):
                annotation = definition.returns
                if isinstance(annotation, ast.Subscript):
                    annotation = annotation.slice
                text = (
                    annotation.value
                    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str)
                    else _dotted(annotation)
                )
                return resolve(target.path, text, "", "", {}) if target else None
        name = _dotted(value)
        return env.get(name) or class_types.get((path, cls, name))

    # Constructor assignments give self.storage and similar fields a local static type.
    for (path, qualified), class_node in definitions.items():
        if class_node.kind != "class":
            continue
        for child in ast.walk(syntax[class_node.id]):
            if isinstance(child, ast.Assign):
                inferred_class = inferred(path, child.value, qualified, qualified, {})
                if inferred_class and inferred_class.kind == "class":
                    for lhs in child.targets:
                        name = _dotted(lhs)
                        if name.startswith("self."):
                            class_types[path, qualified, name] = inferred_class

    for path, (_, tree) in records.items():
        if tree is None:
            continue

        class Calls(ast.NodeVisitor):
            def __init__(self, path: str) -> None:
                self.path = path
                self.scope = ""
                self.cls = ""
                self.owner = file_nodes[self.path]
                self.env: dict[str, GraphNode] = {}

            def visit_definition(
                self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ) -> None:
                qualified = (self.scope + "." + node.name).strip(".")
                target = definitions.get((self.path, qualified))
                if target is None:
                    return
                previous = self.scope, self.cls, self.owner, self.env
                self.scope, self.owner, self.env = qualified, target, dict(self.env)
                if isinstance(node, ast.ClassDef):
                    self.cls = qualified
                self.generic_visit(node)
                self.scope, self.cls, self.owner, self.env = previous

            visit_FunctionDef = visit_definition
            visit_AsyncFunctionDef = visit_definition
            visit_ClassDef = visit_definition

            def visit_Assign(self, node: ast.Assign) -> None:
                target = inferred(self.path, node.value, self.scope, self.cls, self.env)
                for lhs in node.targets:
                    name = _dotted(lhs)
                    if target and target.kind == "class":
                        self.env[name] = target
                    elif name in self.env:
                        del self.env[name]
                self.generic_visit(node)

            def visit_With(self, node: ast.With) -> None:
                for item in node.items:
                    if item.optional_vars:
                        target = inferred(
                            self.path, item.context_expr, self.scope, self.cls, self.env
                        )
                        if target and target.kind == "class":
                            self.env[_dotted(item.optional_vars)] = target
                self.generic_visit(node)

            def visit_Call(self, node: ast.Call) -> None:
                target = resolve(self.path, _dotted(node.func), self.scope, self.cls, self.env)
                if target:
                    self.add(target, node.lineno)
                elif (
                    isinstance(node.func, ast.Call)
                    and isinstance(node.func.func, ast.Attribute)
                    and node.func.func.attr == "command"
                ):
                    for arg in node.args:
                        registered = resolve(
                            self.path, _dotted(arg), self.scope, self.cls, self.env
                        )
                        if registered:
                            self.add(registered, node.lineno)
                else:
                    model.unresolved_calls += 1
                self.generic_visit(node)

            def add(self, target: GraphNode, line: int) -> None:
                key = self.owner.id, target.id, "calls"
                if key not in seen_edges and len(model.graph_edges) < 12000:
                    seen_edges.add(key)
                    model.graph_edges.append(GraphEdge(*key, evidence=f"{self.path}:{line}"))

        Calls(path).visit(tree)
    model.graph_edges = list(
        {
            (e.source, e.target, e.kind): e
            for e in model.graph_edges
            if e.source in model.graph_nodes and e.target in model.graph_nodes
        }.values()
    )
    model.entrypoints = [
        file_nodes[p].id
        for p in sorted(
            entries,
            key=lambda p: (
                Path(p).name
                not in {
                    "main.py",
                    "__main__.py",
                    "index.js",
                    "index.ts",
                    "main.js",
                    "main.ts",
                    "index.tsx",
                    "main.tsx",
                },
                p,
            ),
        )
        if p in file_nodes
    ]
    if not model.entrypoints:
        model.entrypoints = [
            n.id
            for n in file_nodes.values()
            if Path(n.path).name
            in {
                "main.py",
                "app.py",
                "__main__.py",
                "server.py",
                "index.js",
                "index.ts",
                "main.js",
                "main.ts",
                "index.tsx",
                "main.tsx",
            }
        ]
    # Related files and dependency descriptions are computed before views hide nodes.
    for edge in model.graph_edges:
        a, b = model.graph_nodes[edge.source], model.graph_nodes[edge.target]
        a.depends_on.append(f"{b.path}: {b.label}")
        b.used_by.append(f"{a.path}: {a.label}")
        a.related_files.append(b.path)
    for node in model.graph_nodes.values():
        node.related_files = list(dict.fromkeys(node.related_files))[:80]
        node.depends_on = list(dict.fromkeys(node.depends_on))[:80]
        node.used_by = list(dict.fromkeys(node.used_by))[:80]


def _component_name(group: str) -> str:
    if group in {".", ""}:
        return "Project root"
    return group.replace("_", " ").replace("-", " ")


def component_digest(members: list[GraphNode], hashes: dict[str, str]) -> str:
    data = [
        (n.path, hashes[n.path], n.ai_explanation) for n in sorted(members, key=lambda n: n.path)
    ]
    import json

    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _component_groups(files: list[GraphNode]) -> dict[str, list[GraphNode]]:
    groups: dict[str, list[GraphNode]] = {}
    for n in files:
        parts = Path(n.path).parts
        group = "/".join(parts[:2]) if len(parts) > 2 else str(Path(n.path).parent)
        groups.setdefault(group, []).append(n)
    # Split large folder groups only where real subfolders exist. Small projects
    # may have fewer than five components; never manufacture components.
    while len(groups) < 5:
        choices = []
        for group, members in groups.items():
            split: dict[str, list[GraphNode]] = {}
            for n in members:
                parent = str(Path(n.path).parent)
                key = parent if parent != group else group
                split.setdefault(key, []).append(n)
            if len(split) > 1 and len(groups) - 1 + len(split) <= 8:
                choices.append((len(members), group, split))
        if not choices:
            break
        _, key, split = max(choices, key=lambda x: (x[0], x[1]))
        del groups[key]
        groups.update(split)
    if len(groups) > 8:
        ordered = sorted(groups, key=lambda g: (-len(groups[g]), g))
        kept = {g: groups[g] for g in ordered[:7]}
        kept["Other project parts"] = [n for g in ordered[7:] for n in groups[g]]
        groups = kept
    return groups


def _language_imports(
    root: Path, model: ProjectMap, records: dict[str, tuple[str, ast.Module | None]]
) -> None:
    """Resolve only literal local Dart/JS imports. Never guess an edge from folders."""
    import posixpath

    files = {n.path: n for n in model.graph_nodes.values() if n.kind in {"module", "file"}}
    options = read_json_config(root / "tsconfig.json", root).get("compilerOptions", {})
    if not options:
        options = read_json_config(root / "jsconfig.json", root).get("compilerOptions", {})
    package = ""
    manifest = root / "pubspec.yaml"
    if manifest.is_file() and not manifest.is_symlink():
        with manifest.open() as stream:
            text = stream.read(65536)
        found = re.search(r"^name:\s*([\w-]+)", text, re.M)
        package = found[1] if found else ""
        if "lib/main.dart" in files:
            model.entrypoints.insert(0, files["lib/main.dart"].id)
    languages = set()
    for path, (source, tree) in records.items():
        suffix = Path(path).suffix
        language = {
            ".py": "Python",
            ".dart": "Dart",
            ".js": "JavaScript",
            ".jsx": "JavaScript",
            ".ts": "TypeScript",
            ".tsx": "TypeScript",
        }.get(suffix, suffix.lstrip("."))
        languages.add(language)
        if tree is not None:
            continue
        # Strip comments without eating quoted URI strings.
        clean = re.sub(
            r"/\*.*?\*/|//[^\n]*|(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')",
            lambda m: " " if m[0].startswith(("//", "/*")) else m[0],
            source,
            flags=re.S,
        )
        if suffix == ".dart":
            pattern = r"^\s*import\s+['\"]([^'\"]+)['\"]"
        elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
            pattern = r"(?:\bfrom\s*|\bimport\s*|\brequire\(\s*)['\"]([^'\"]+)['\"]"
        else:
            continue
        quoted = list(
            re.finditer(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`", clean, re.S)
        )
        imports = [
            m[1]
            for m in re.finditer(pattern, clean, re.M)
            if not any(q.start() < m.start() < q.end() for q in quoted)
        ]
        for uri in imports:
            targets: list[str] = []
            if uri.startswith("package:") and package and uri.startswith(f"package:{package}/"):
                target = "lib/" + uri.split("/", 1)[1]
            elif not uri.startswith(("package:", "dart:", "/")) and (
                suffix == ".dart" or uri.startswith(".")
            ):
                target = posixpath.normpath(posixpath.join(str(Path(path).parent), uri))
            elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
                targets = import_targets(root, uri, options)
                target = ""
            else:
                continue
            targets = targets if not target else [target]
            candidates = []
            for base in targets:
                candidates.append(base)
                if suffix != ".dart":
                    candidates.extend(base + ext for ext in (".ts", ".tsx", ".js", ".jsx"))
                    candidates.extend(
                        base + "/index" + ext for ext in (".ts", ".tsx", ".js", ".jsx")
                    )
            resolved = next((p for p in candidates if p in files), None)
            if resolved and resolved != path:
                a, b = files[path], files[resolved]
                model.graph_edges.append(GraphEdge(a.id, b.id, "imports", path))
                a.depends_on.append(resolved)
                b.used_by.append(path)
                a.related_files.append(resolved)
    for n in files.values():
        n.depends_on = list(dict.fromkeys(n.depends_on))
        n.used_by = list(dict.fromkeys(n.used_by))
        n.related_files = list(dict.fromkeys(n.related_files))
    model.detected_languages = sorted(languages)
    model.entrypoints.extend(n.id for n in files.values() if framework_label(n.path) or n.routes)
    model.entrypoints = list(dict.fromkeys(model.entrypoints))


def graph_view(
    model: ProjectMap, view: str = "overview", module: str | None = None, limit: int = 24
) -> dict[str, Any]:
    """Produce one capped JSON view; all relationships originate in scanned source."""
    if view not in {"overview", "module", "flow"} or not 2 <= limit <= 60:
        raise UserError("Choose overview, module or flow and a node limit between 2 and 60.")
    file_nodes = {n.path: n for n in model.graph_nodes.values() if n.kind in {"module", "file"}}
    if module is not None and module not in file_nodes:
        raise UserError("Choose a file from the project's analyzed modules.")
    nodes, edges = dict(model.graph_nodes), list(model.graph_edges)
    file_edges: dict[tuple[str, str, str], GraphEdge] = {}
    for e in edges:
        a, b = nodes[e.source], nodes[e.target]
        if a.path != b.path and a.path in file_nodes and b.path in file_nodes:
            key = file_nodes[a.path].id, file_nodes[b.path].id, e.kind
            file_edges[key] = GraphEdge(*key, evidence=e.evidence)
    if view == "overview":
        groups = _component_groups(list(file_nodes.values()))
        projected: dict[str, str] = {}
        nodes = {}
        for group, members in groups.items():
            members.sort(key=lambda n: importance(n.path, []))
            digest = component_digest(members, model.hashes)
            summary = next(
                (
                    r
                    for r in model.component_summaries
                    if r["path"] == group and r["content_hash"] == digest
                ),
                None,
            )
            ai = {"label": "AI-written", "text": summary["explanation"]} if summary else None
            purpose = ai["text"] if ai else "Not explained yet"
            source = "AI-written" if ai else "offline"
            name = _component_name(group)
            node = GraphNode(
                node_id("folder", group),
                "folder",
                name,
                group,
                group,
                purpose,
                ai_explanation=ai,
                related_files=[n.path for n in members],
                depends_on=list(dict.fromkeys(t for n in members for t in n.depends_on))[:80],
                used_by=list(dict.fromkeys(t for n in members for t in n.used_by))[:80],
                file_count=len(members),
                purpose_source=source,
                explained_files=sum(bool(n.ai_explanation) for n in members),
            )
            nodes[node.id] = node
            for member in members:
                projected[member.id] = node.id
        edges = [
            GraphEdge(projected[e.source], projected[e.target], "imports", e.evidence)
            for e in file_edges.values()
            if e.kind == "imports" and projected[e.source] != projected[e.target]
        ]
        ranked = sorted(
            nodes,
            key=lambda i: (-sum(e.source == i or e.target == i for e in edges), nodes[i].path),
        )
    elif view == "module":
        target = file_nodes.get(module or "") or next(
            (n for n in file_nodes.values() if n.id in model.entrypoints),
            next(iter(file_nodes.values()), None),
        )
        module = target.path if target else module
        own = {i for i, n in nodes.items() if n.path == module}
        selected = set(own)
        projected_edges = []
        for edge in edges:
            if edge.source not in own:
                continue
            destination = nodes[edge.target]
            target_id = (
                edge.target if destination.path == module else file_nodes[destination.path].id
            )
            selected.add(target_id)
            projected_edges.append(GraphEdge(edge.source, target_id, edge.kind, edge.evidence))
        nodes = {i: n for i, n in nodes.items() if i in selected}
        edges = projected_edges
        degrees = Counter(i for e in edges for i in (e.source, e.target))
        ranked = sorted(
            nodes,
            key=lambda i: (
                not (nodes[i].path == module and nodes[i].kind in {"module", "file"}),
                nodes[i].path != module,
                nodes[i].label.count(".") > 1,
                nodes[i].label.split(".")[-1].startswith("_"),
                -degrees[i],
                nodes[i].line or 0,
                nodes[i].label,
            ),
        )
    else:
        roots = [file_nodes[module].id] if module else list(model.entrypoints)
        if not roots and file_nodes:
            roots = [next(iter(file_nodes.values())).id]
            module = next(iter(file_nodes))
        nodes = {n.id: n for n in file_nodes.values()}
        edges = list(file_edges.values())
        degrees = Counter(i for e in edges for i in (e.source, e.target))
        parents: dict[str, str | None] = dict.fromkeys(roots)
        queue = list(roots)
        for current in queue:
            neighbors = sorted(
                (e for e in edges if e.source == current),
                key=lambda e: (e.kind != "calls", -degrees[e.target], nodes[e.target].path),
            )
            for edge in neighbors:
                if edge.target not in parents:
                    parents[edge.target] = current
                    queue.append(edge.target)
        nodes = {i: n for i, n in nodes.items() if i in parents}
        edges = [e for e in edges if e.source in nodes and e.target in nodes]
        backbone = list(roots)
        for category in ("service", "storage", "providers"):
            candidates = [
                i
                for i, n in nodes.items()
                if (
                    Path(n.path).stem == "service"
                    if category == "service"
                    else category in Path(n.path).parts
                )
            ]
            for target_id in sorted(candidates, key=lambda i: -degrees[i])[:2]:
                route = []
                cursor: str | None = target_id
                while cursor is not None:
                    route.append(cursor)
                    cursor = parents[cursor]
                backbone.extend(reversed(route))
        ranked = sorted(
            nodes,
            key=lambda i: (
                i not in roots,
                not (
                    set(Path(nodes[i].path).parts) & {"storage", "providers"}
                    or Path(nodes[i].path).name == "service.py"
                ),
                importance(nodes[i].path, []),
            ),
        )
        ranked = list(dict.fromkeys([*backbone, *ranked]))
    total = len(nodes)
    omitted = max(total - limit + 1, 0) if total > limit else 0
    visible = ranked[: limit - 1] if omitted else ranked
    displayed = {i: nodes[i] for i in visible}
    collapsed = node_id("collapsed", view, module or "")
    if omitted:
        displayed[collapsed] = GraphNode(
            collapsed,
            "collapsed",
            f"+{omitted} more",
            "",
            "",
            f"{omitted} other parts are hidden in this bounded view.",
        )
    counted: Counter[tuple[str, str, str]] = Counter()
    for e in edges:
        source_id = e.source if e.source in displayed else collapsed
        destination_id = e.target if e.target in displayed else collapsed
        if (
            (
                source_id != destination_id
                or (view == "module" and e.source == e.target and source_id != collapsed)
            )
            and source_id in displayed
            and destination_id in displayed
        ):
            counted[source_id, destination_id, e.kind] += 1
    capped_edges = sorted(counted)[: min(limit * 6, 240)]
    result = {
        "view": view,
        "module": module,
        "nodes": [asdict(n) for n in displayed.values()],
        "edges": [asdict(GraphEdge(*key, count=counted[key])) for key in capped_edges],
        "meta": {
            "scanned_files": model.scanned,
            "skipped_files": model.skipped,
            "total_nodes": total,
            "shown_nodes": len(displayed),
            "hidden_nodes": omitted,
            "hidden_edges": max(0, len(counted) - len(capped_edges)),
            "node_limit": limit,
            "limited": model.limited or bool(omitted) or len(counted) > len(capped_edges),
            "unresolved_calls": model.unresolved_calls,
            "available_modules": sorted(file_nodes),
            "entrypoints": [model.graph_nodes[i].path for i in model.entrypoints],
            "file_count": len(file_nodes),
            "import_support": (
                "Python; Dart/Flutter (package: and relative); "
                "JavaScript/TypeScript (relative/tsconfig/jsconfig aliases, "
                "ES imports and require)."
            ),
            "entrypoint_support": (
                "Python package scripts and main/app/server files; "
                "Flutter lib/main.dart with pubspec.yaml; "
                "JavaScript/TypeScript package main/bin and index/main files; "
                "Next app-router pages/layouts/handlers; literal Express/Flask routes."
            ),
            "detected_languages": model.detected_languages,
            "flow_notice": (
                "No entry points detected. Pick a starting file to explore its detected imports."
                if not model.entrypoints
                else "Possible paths from a starting file, not a running request."
            ),
            "connections_notice": (
                ""
                if any(e.kind == "imports" for e in model.graph_edges)
                else "connections not detected for this language"
            ),
            "notice": (
                "Paths found in source code, not an execution trace. "
                "Dynamic calls and non-Python internals may be missing."
            ),
        },
    }
    validate_graph(result)
    return result


def validate_graph(graph: dict[str, Any]) -> None:
    if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise UserError("A graph must contain node and edge lists.")
    for node in graph["nodes"]:
        if not isinstance(node, dict) or any(
            not isinstance(node.get(k), str)
            for k in ("id", "kind", "label", "path", "group", "purpose")
        ):
            raise UserError("Invalid graph node fields.")
        ai = node.get("ai_explanation")
        if ai is not None and (not isinstance(ai, dict) or not isinstance(ai.get("text"), str)):
            raise UserError("Invalid AI explanation.")
    if any(
        not isinstance(e, dict)
        or any(not isinstance(e.get(k), str) for k in ("source", "target", "kind"))
        for e in graph["edges"]
    ):
        raise UserError("Invalid graph edge fields.")
    ids = [n["id"] for n in graph["nodes"]]
    if (
        len(ids) != len(set(ids))
        or len(ids) > 60
        or not all(re.fullmatch(r"n_[a-f0-9]{20}", i) for i in ids)
    ):
        raise UserError("Invalid graph node IDs or node limit.")
    for node in graph["nodes"]:
        if node["kind"] not in {"module", "file", "folder", "function", "class", "collapsed"}:
            raise UserError("Invalid graph node kind.")
        if node["path"] and (Path(node["path"]).is_absolute() or ".." in Path(node["path"]).parts):
            raise UserError("Graph paths must stay inside the project.")
        if node.get("ai_explanation") and node["ai_explanation"].get("label") != "AI-written":
            raise UserError("AI explanations must retain their provenance label.")
    if any(
        e["source"] not in ids or e["target"] not in ids or e["kind"] not in {"imports", "calls"}
        for e in graph["edges"]
    ):
        raise UserError("Invalid graph edge.")


def _mermaid_text(value: str) -> str:
    # Encode Mermaid's structural characters before placing text inside a quoted label.
    return "".join(
        f"#{ord(c)};" if c in '&#"<>[]{}|`\\%;' else " " if c.isspace() or ord(c) < 32 else c
        for c in value
    )


def export_mermaid(graph: dict[str, Any], *, include_ai: bool = False) -> str:
    validate_graph(graph)
    lines = ["flowchart TD"]
    for node in graph["nodes"]:
        purpose = node["purpose"][:180]
        if include_ai and node.get("ai_explanation"):
            purpose = "AI-written: " + node["ai_explanation"]["text"][:180]
        text = _mermaid_text(node["label"][:120] + " — " + purpose)
        lines.append(f'  {node["id"]}["{text}"]')
    for edge in graph["edges"]:
        lines.append(f"  {edge['source']} -->|{edge['kind']}| {edge['target']}")
    result = "\n".join(lines) + "\n"
    validate_mermaid(result)
    return result


def validate_mermaid(text: str) -> None:
    """Validate the deliberately small Mermaid grammar emitted by this exporter."""
    lines = text.splitlines()
    if not lines or lines[0] != "flowchart TD":
        raise UserError("Invalid Mermaid graph header.")
    names: set[str] = set()
    for line in lines[1:]:
        node = re.fullmatch(r'  (n_[a-f0-9]{20})\["([^"<>]*)"\]', line)
        edge = re.fullmatch(r"  (n_[a-f0-9]{20}) -->\|(imports|calls)\| (n_[a-f0-9]{20})", line)
        if node:
            if node[1] in names:
                raise UserError("Duplicate Mermaid node.")
            names.add(node[1])
        elif not edge or edge[1] not in names or edge[3] not in names:
            raise UserError("Invalid Mermaid graph syntax or edge target.")
