"""Conservative validation of separately labeled beginner explanations."""

from __future__ import annotations

import ast
import re
from pathlib import Path


def allowed_names(path: str, source: str, symbols: list[str], imports: list[str]) -> set[str]:
    names = {path, Path(path).name, *symbols, *imports}
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.update((alias.name, alias.asname or alias.name))
            elif isinstance(node, ast.Attribute):
                names.add(ast.unparse(node))
    except (SyntaxError, ValueError, RecursionError):
        pass
    names.update(n.split(".")[-1] for n in list(names) if "/" not in n)
    names.update(
        n + "()"
        for n in list(names)
        if n not in {path, Path(path).name} and re.fullmatch(r"\w+(?:\.\w+)*", n)
    )
    return names


def valid_explanation(text: str, references: list[str], allowed: set[str]) -> bool:
    if not text.strip() or len(text.split()) > 100 or "\n" in text:
        return False
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip())
    if not 1 <= len(sentences) <= 2 or not text.rstrip().endswith((".", "!", "?")):
        return False
    quoted = set(re.findall(r"`([^`]+)`", text))
    if text.count("`") % 2 or not set(references) <= allowed or not quoted <= allowed:
        return False
    if quoted != set(references):
        return False
    prose = re.sub(r"`[^`]+`", "", text)
    # Code-shaped names must also be recognized; reject unquoted invented names.
    shaped = set(
        re.findall(
            r"\b(?:[A-Za-z_]\w*(?:[./:]\w+)+|\w+_\w+|[A-Z][a-z]+[A-Z]\w*|\w+(?=\())\b", prose
        )
    )
    contextual = set(
        re.findall(
            r"\b(?:function|class|method|file|module)\s+[\"']?(\w+(?:[./]\w+)*)", prose, re.I
        )
    )
    contextual.update(
        re.findall(r"\b([A-Z]\w*|\w+_\w+)\s+(?:function|class|method|file|module)\b", prose)
    )
    contextual -= {
        "that",
        "which",
        "is",
        "to",
        "and",
        "for",
        "with",
        "in",
        "as",
        "by",
        "handles",
        "takes",
        "returns",
        "uses",
        "reads",
        "writes",
        "loads",
        "saves",
        "creates",
        "builds",
        "turns",
        "provides",
        "holds",
        "runs",
        "calls",
        "keeps",
        "contains",
        "connects",
        "converts",
        "prepares",
        "stores",
        "checks",
        "collects",
        "describes",
        "groups",
        "defines",
        "renders",
        "displays",
        "tracks",
        "calculates",
        "lets",
        "helps",
        "gathers",
        "organizes",
        "combines",
        "makes",
        "gets",
        "sets",
        "has",
        "can",
        "will",
        "does",
        "would",
        "should",
        "may",
        "must",
        "works",
        "you",
        "we",
        "a",
        "an",
        "the",
        "This",
        "The",
        "These",
        "Each",
        "Python",
        "JavaScript",
    }
    capitals = set(re.findall(r"\b[A-Z][A-Za-z]+\b", prose))
    capitals -= {
        "The",
        "This",
        "It",
        "A",
        "An",
        "These",
        "Each",
        "When",
        "You",
        "For",
        "In",
        "If",
        "After",
        "Before",
        "JSON",
        "API",
        "HTTP",
        "SQL",
        "Python",
        "JavaScript",
        "Data",
        "Returns",
        "Creates",
        "Reads",
        "Writes",
    }
    return (shaped | contextual | capitals) <= allowed
