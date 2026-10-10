"""Name -> adapter lookup."""

from __future__ import annotations

from pathlib import Path

from contextbridge.adapters.base_adapter import BaseAdapter
from contextbridge.adapters.claude_adapter import ClaudeAdapter
from contextbridge.adapters.cursor_adapter import CursorAdapter
from contextbridge.adapters.generic_adapter import GenericAdapter
from contextbridge.errors import UnknownAgentError

ADAPTERS: dict[str, type[BaseAdapter]] = {
    "claude": ClaudeAdapter,
    "cursor": CursorAdapter,
    "generic": GenericAdapter,
}
# Agents without a dedicated adapter use the generic one.
ALIASES: dict[str, str] = {
    "claude-code": "claude",
    "aider": "generic",
    "opencode": "generic",
}


def valid_names() -> list[str]:
    return sorted([*ADAPTERS, *ALIASES])


def resolve_name(name: str) -> str:
    """Canonical adapter name for ``name`` (case-insensitive), or UnknownAgentError."""
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    if key not in ADAPTERS:
        raise UnknownAgentError(
            f"Unknown agent {name.strip()!r}.",
            hint=(f"Valid agents: {', '.join(ADAPTERS)} (aliases: {', '.join(sorted(ALIASES))})."),
        )
    return key


def get_adapter(name: str, repo_root: Path, *, force: bool = False) -> BaseAdapter:
    return ADAPTERS[resolve_name(name)](repo_root, force=force)


def all_adapters(repo_root: Path) -> list[BaseAdapter]:
    return [cls(repo_root) for cls in ADAPTERS.values()]
