"""Map diff hunks to the code symbols they touch, using tree-sitter.

For each changed file, the old and new versions are parsed and every changed line is
attributed to its innermost enclosing symbol. The result is a list of
``{"name", "kind", "change", "signature"}`` dicts where kind is
``function|class|method|export`` and change is ``added|modified|removed``.

Fallbacks, in order: tree-sitter symbols -> hunk-header context (``@@ ... @@ def foo``)
-> file level (empty list). Nothing in this module raises to the caller.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import PurePosixPath
from typing import Any

log = logging.getLogger("contextbridge")

MAX_SYMBOLS_PER_FILE = 50
MAX_SIGNATURE_CHARS = 160

LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
}


def detect_language(path: str) -> str | None:
    return LANGUAGE_BY_EXTENSION.get(PurePosixPath(path).suffix.lower())


@dataclass(frozen=True)
class Symbol:
    name: str  # Qualified, e.g. "Cart.total" or "Server::run"
    kind: str  # function | class | method | export
    start_line: int  # 1-based, inclusive
    end_line: int
    signature: str
    text: str = field(repr=False)
    parent: str | None = None


# ----- diff parsing --------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    context: str
    removed: list[int] = field(default_factory=list)  # Old-file line numbers.
    added: list[int] = field(default_factory=list)  # New-file line numbers.


def parse_hunks(patch: str) -> list[Hunk]:
    """Parse a unified diff into hunks with the exact changed line numbers."""
    hunks: list[Hunk] = []
    old_ln = new_ln = 0
    for line in patch.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            old_ln, new_ln = int(m.group(1)), int(m.group(3))
            hunks.append(
                Hunk(
                    old_start=old_ln,
                    old_count=int(m.group(2) or 1),
                    new_start=new_ln,
                    new_count=int(m.group(4) or 1),
                    context=m.group(5).strip(),
                )
            )
            continue
        if not hunks:
            continue
        hunk = hunks[-1]
        if line.startswith("+"):
            hunk.added.append(new_ln)
            new_ln += 1
        elif line.startswith("-"):
            hunk.removed.append(old_ln)
            old_ln += 1
        elif line.startswith(" ") or line == "":
            old_ln += 1
            new_ln += 1
        # "\ No newline at end of file" and anything else: no line consumed.
    return hunks


# ----- symbol extraction -----------------------------------------------------------


@cache
def _parser(language: str) -> Any:
    from tree_sitter_language_pack import get_parser

    return get_parser(language)  # type: ignore[arg-type]


def _text(node: Any, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _name_of(node: Any, src: bytes, fieldname: str = "name") -> str | None:
    child = node.child_by_field_name(fieldname)
    return _text(child, src) if child is not None else None


def _signature(node: Any, src: bytes) -> str:
    """Declaration text up to the body, collapsed to one line."""
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None and body.start_byte > node.start_byte else None
    raw = src[node.start_byte : end if end is not None else node.end_byte]
    text = raw.decode("utf-8", errors="replace")
    if end is None:
        text = text.split("\n", 1)[0]
    text = " ".join(text.split()).rstrip(" {:")
    if len(text) > MAX_SIGNATURE_CHARS:
        text = text[: MAX_SIGNATURE_CHARS - 3] + "..."
    return text


_GENERIC_ARGS = re.compile(r"<.*$", re.S)


def _type_base_name(text: str) -> str:
    """`*Server` / `Vec<T>` / `S[T]` -> `Server` / `Vec` / `S`."""
    text = _GENERIC_ARGS.sub("", text.strip().lstrip("*&").strip())
    return text.split("[", 1)[0].strip()


@dataclass(frozen=True)
class _Found:
    """A symbol candidate before qualification: what the walker discovered at a node."""

    name: str
    kind: str
    node: Any  # Node whose range defines the symbol (includes decorators/export).
    sig_node: Any
    scope: str | None = None  # Scope name pushed for children (classes, impls, mods).
    emit: bool = True  # False for pure containers (Rust impl/mod).


_JS_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
_JS_CLASSES = {"class_declaration", "abstract_class_declaration", "class"}
_TS_CLASS_LIKE = {"interface_declaration", "enum_declaration"}


def _python(node: Any, src: bytes, in_class: bool) -> _Found | None:
    outer = node
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        if inner is None:
            return None
        node = inner
    name = _name_of(node, src)
    if name is None:
        return None
    if node.type == "function_definition":
        return _Found(name, "method" if in_class else "function", outer, node, scope=name)
    if node.type == "class_definition":
        return _Found(name, "class", outer, node, scope=name)
    return None


def _js(node: Any, src: bytes, in_class: bool, exported: bool = False) -> _Found | None:
    t = node.type
    if t == "export_statement":
        decl = node.child_by_field_name("declaration")
        if decl is not None:
            found = _js(decl, src, in_class, exported=True)
            if found is not None:
                # Use the export statement's range so `export` edits count.
                return _Found(found.name, found.kind, node, found.sig_node, found.scope)
            return None
        value = node.child_by_field_name("value")
        if value is not None:
            kind = "class" if value.type in _JS_CLASSES else "export"
            return _Found("default", kind, node, value, scope="default")
        return None
    if t in ("function_declaration", "generator_function_declaration"):
        name = _name_of(node, src)
        return _Found(name, "function", node, node, scope=name) if name else None
    if t in _JS_CLASSES or t in _TS_CLASS_LIKE:
        name = _name_of(node, src)
        return _Found(name, "class", node, node, scope=name) if name else None
    if t == "type_alias_declaration" and exported:
        name = _name_of(node, src)
        return _Found(name, "export", node, node) if name else None
    if t == "method_definition":
        name = _name_of(node, src)
        return _Found(name, "method", node, node, scope=name) if name else None
    if t in ("public_field_definition", "field_definition") and in_class:
        value = node.child_by_field_name("value")
        name = _name_of(node, src) or _name_of(node, src, "property")
        if name and value is not None and value.type in _JS_FUNCTION_VALUES:
            return _Found(name, "method", node, value, scope=name)
        return None
    if t in ("lexical_declaration", "variable_declaration"):
        declarators = [c for c in node.named_children if c.type == "variable_declarator"]
        if len(declarators) != 1:
            return None
        decl = declarators[0]
        name = _name_of(decl, src)
        value = decl.child_by_field_name("value")
        if name is None:
            return None
        if value is not None and value.type in _JS_FUNCTION_VALUES:
            return _Found(name, "function", node, node, scope=name)
        if value is not None and value.type in _JS_CLASSES:
            return _Found(name, "class", node, node, scope=name)
        if exported:
            return _Found(name, "export", node, node)
    return None


def _go(node: Any, src: bytes) -> _Found | None:
    t = node.type
    if t == "function_declaration":
        name = _name_of(node, src)
        return _Found(name, "function", node, node) if name else None
    if t == "method_declaration":
        name = _name_of(node, src)
        receiver = node.child_by_field_name("receiver")
        recv_type = None
        if receiver is not None:
            for param in receiver.named_children:
                type_node = param.child_by_field_name("type")
                if type_node is not None:
                    recv_type = _type_base_name(_text(type_node, src))
                    break
        if name is None:
            return None
        full = f"{recv_type}.{name}" if recv_type else name
        return _Found(full, "method", node, node)
    if t == "type_spec":
        name = _name_of(node, src)
        type_node = node.child_by_field_name("type")
        if name and type_node is not None and type_node.type in ("struct_type", "interface_type"):
            # Range of the whole `type X struct {...}` declaration if it holds one spec.
            parent = node.parent
            outer = parent if parent is not None and parent.named_child_count == 1 else node
            return _Found(name, "class", outer, node)
    return None


def _rust(node: Any, src: bytes, in_impl: bool) -> _Found | None:
    t = node.type
    if t == "function_item":
        name = _name_of(node, src)
        return _Found(name, "method" if in_impl else "function", node, node) if name else None
    if t in ("struct_item", "enum_item", "trait_item", "union_item"):
        name = _name_of(node, src)
        return _Found(name, "class", node, node, scope=name) if name else None
    if t == "impl_item":
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return None
        name = _type_base_name(_text(type_node, src))
        return _Found(name, "class", node, node, scope=name, emit=False)
    if t == "mod_item":
        name = _name_of(node, src)
        return _Found(name, "class", node, node, scope=name, emit=False) if name else None
    return None


_SEPARATOR = {"rust": "::"}
_CLASS_SCOPES = {
    "class_definition",
    "class_declaration",
    "abstract_class_declaration",
    "class",
    "impl_item",
    "trait_item",
}


def extract_symbols(source: str, language: str) -> list[Symbol]:
    """Return every symbol (functions, classes, methods, exports) in ``source``."""
    src = source.encode("utf-8", errors="replace")
    tree = _parser(language).parse(src)
    sep = _SEPARATOR.get(language, ".")
    symbols: list[Symbol] = []

    def visit(node: Any, scope: list[str], parent: str | None, in_class: bool) -> None:
        for child in node.named_children:
            if language == "python":
                found = _python(child, src, in_class)
            elif language in ("javascript", "typescript", "tsx"):
                found = _js(child, src, in_class)
            elif language == "go":
                found = _go(child, src)
            else:
                found = _rust(child, src, in_class)

            if found is None:
                visit(child, scope, parent, in_class)
                continue
            qualified = sep.join([*scope, found.name])
            if found.emit:
                symbols.append(
                    Symbol(
                        name=qualified,
                        kind=found.kind,
                        start_line=found.node.start_point[0] + 1,
                        end_line=found.node.end_point[0] + 1,
                        signature=_signature(found.sig_node, src),
                        text=_text(found.node, src),
                        parent=parent,
                    )
                )
            if found.scope is None:
                continue
            child_scope = [*scope, found.scope] if found.scope else scope
            def_node = found.sig_node
            visit(
                def_node if def_node is not found.node else found.node,
                child_scope,
                qualified if found.emit else parent,
                def_node.type in _CLASS_SCOPES,
            )

    visit(tree.root_node, [], None, False)
    return symbols


# ----- mapping hunks to symbols ------------------------------------------------------------


def _innermost(symbols: list[Symbol], line: int) -> Symbol | None:
    best: Symbol | None = None
    for sym in symbols:
        if sym.start_line <= line <= sym.end_line and (
            best is None or sym.end_line - sym.start_line < best.end_line - best.start_line
        ):
            best = sym
    return best


def _by_name(symbols: list[Symbol]) -> dict[str, Symbol]:
    out: dict[str, Symbol] = {}
    for sym in symbols:
        out.setdefault(sym.name, sym)
    return out


def _entry(sym: Symbol, change: str) -> dict[str, str]:
    return {"name": sym.name, "kind": sym.kind, "change": change, "signature": sym.signature}


def map_hunks_to_symbols(
    old_symbols: list[Symbol], new_symbols: list[Symbol], hunks: list[Hunk]
) -> list[dict[str, str]]:
    """Attribute changed lines to symbols and classify each as added/modified/removed."""
    old_map, new_map = _by_name(old_symbols), _by_name(new_symbols)
    results: dict[str, dict[str, str]] = {}

    added_names = [n for n in new_map if n not in old_map]
    removed_names = [n for n in old_map if n not in new_map]
    for name in added_names:
        sym = new_map[name]
        # Report a new class, not each of its new methods as well.
        if sym.parent is None or sym.parent not in added_names:
            results[name] = _entry(sym, "added")
    for name in removed_names:
        sym = old_map[name]
        if sym.parent is None or sym.parent not in removed_names:
            results[name] = _entry(sym, "removed")

    touched: list[str] = []
    for hunk in hunks:
        for line in hunk.added:
            hit = _innermost(new_symbols, line)
            if hit is not None:
                touched.append(hit.name)
        for line in hunk.removed:
            hit = _innermost(old_symbols, line)
            if hit is not None:
                touched.append(hit.name)
    for name in dict.fromkeys(touched):
        if name in results or name not in old_map or name not in new_map:
            continue
        if old_map[name].text != new_map[name].text:
            results[name] = _entry(new_map[name], "modified")

    # Keep source order: new-file order first, then removed symbols.
    order = {s.name: i for i, s in enumerate(new_symbols)}
    order.update({n: len(order) + i for i, n in enumerate(removed_names)})
    ordered = sorted(results.values(), key=lambda e: order.get(e["name"], 1 << 30))
    return ordered[:MAX_SYMBOLS_PER_FILE]


# ----- fallback: hunk-header context -------------------------------------------------------

_CONTEXT_DECL = re.compile(
    r"\b(?P<kw>def|class|func|function|fn|impl|struct|enum|trait|interface|type)\s+"
    r"(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_$][\w$]*)"
)


def symbols_from_hunk_context(hunks: list[Hunk]) -> list[dict[str, str]]:
    """Derive approximate symbols from the ``@@ ... @@ <context>`` text git provides."""
    out: dict[str, dict[str, str]] = {}
    for hunk in hunks:
        if not hunk.context:
            continue
        m = _CONTEXT_DECL.search(hunk.context)
        if m is None:
            continue
        kw = m.group("kw")
        kind = (
            "class"
            if kw in {"class", "struct", "enum", "trait", "interface", "impl"}
            else ("function")
        )
        name = m.group("name")
        out.setdefault(
            name,
            {
                "name": name,
                "kind": kind,
                "change": "modified",
                "signature": hunk.context[:MAX_SIGNATURE_CHARS],
            },
        )
    return list(out.values())[:MAX_SYMBOLS_PER_FILE]


def _top_level(symbols: list[Symbol]) -> Iterator[Symbol]:
    return (s for s in symbols if s.parent is None)


def symbols_for_change(
    path: str,
    old_source: str | None,
    new_source: str | None,
    patch: str,
) -> list[dict[str, str]]:
    """Symbols touched by one file change. Never raises; degrades to [] (file level)."""
    try:
        hunks = parse_hunks(patch)
    except Exception:  # pragma: no cover - defensive
        log.debug("ast: cannot parse hunks for %s", path, exc_info=True)
        return []
    language = detect_language(path)
    if language is not None:
        try:
            old_syms = extract_symbols(old_source, language) if old_source is not None else []
            new_syms = extract_symbols(new_source, language) if new_source is not None else []
            if new_source is None:  # Deleted file.
                return [_entry(s, "removed") for s in _top_level(old_syms)][:MAX_SYMBOLS_PER_FILE]
            if old_source is None:  # Added file.
                return [_entry(s, "added") for s in _top_level(new_syms)][:MAX_SYMBOLS_PER_FILE]
            mapped = map_hunks_to_symbols(old_syms, new_syms, hunks)
            if mapped or old_syms or new_syms:
                return mapped
        except Exception:
            log.warning("ast: parsing %s failed; using hunk context", path, exc_info=True)
    try:
        return symbols_from_hunk_context(hunks)
    except Exception:  # pragma: no cover - defensive
        return []
