"""Plain-language labels for the app. No Qt here, so it is easy to test.

The command line says "session", "tokens" and "symbols"; the app says "notes", "words"
and "functions". Every user-facing phrase that maps onto a technical term lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from contextbridge.storage.db import TestRun, parse_utc


@dataclass(frozen=True)
class NoteKind:
    key: str  # the stored tag
    label: str
    hint: str


NOTE_KINDS: tuple[NoteKind, ...] = (
    NoteKind("note", "Note", "Anything worth remembering"),
    NoteKind("architecture", "Decision", "A choice you made, and why"),
    NoteKind("blocker", "Stuck on", "Something standing in your way"),
    NoteKind("todo", "To-do", "What to do next"),
    NoteKind("bug", "Bug", "Something that's broken"),
)
NOTE_LABELS = {k.key: k.label for k in NOTE_KINDS}


@dataclass(frozen=True)
class AgentChoice:
    key: str  # the adapter name
    label: str
    blurb: str
    file: str  # shown so nothing is a surprise


AGENTS: tuple[AgentChoice, ...] = (
    AgentChoice("claude", "Claude Code", "Claude reads this file on its own.", "CLAUDE.md"),
    AgentChoice("cursor", "Cursor", "Adds a rule Cursor always follows.", ".cursor/rules/handoff.mdc"),
    AgentChoice("generic", "Another tool", "For Aider, OpenCode and others.", "HANDOFF.md"),
)  # fmt: skip
AGENT_LABELS = {a.key: a.label for a in AGENTS}
SOURCE_CHOICES: tuple[tuple[str, str], ...] = (
    *((a.key, a.label) for a in AGENTS[:2]),
    ("another tool", "Another tool"),
)

CHANGE_LABELS = {"added": "New", "modified": "Changed", "deleted": "Removed", "renamed": "Renamed"}
WORDS_PER_TOKEN = 0.75


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def words(tokens: int) -> str:
    """Approximate size in plain words ("about 180 words")."""
    n = max(round(tokens * WORDS_PER_TOKEN / 10) * 10, 10) if tokens > 0 else 0
    return f"about {n} words" if n else "no words"


def size_sentence(raw_tokens: int, tokens: int) -> str:
    """How long the summary is, compared with everything it replaces."""
    short = words(tokens)
    if raw_tokens <= 0:
        return f"The summary is {short} long."
    if tokens >= raw_tokens:
        return f"The summary is {short} long. (This project is small, so there's little to shorten yet.)"
    saved = round((1 - tokens / raw_tokens) * 100)
    return f"The summary is {short} long, down from {words(raw_tokens)}: about {saved}% shorter."


def how_written(method: str, attempts: list[tuple[str, str]], model: str = "") -> str:
    """One calm sentence on how the summary was produced."""
    if method == "deterministic":
        if attempts:
            return "Written from a template (your AI wasn't reachable, so nothing was lost)."
        return "Written from a template. No AI was needed, and nothing left your computer."
    if method == "ollama":
        return (
            f"Written by your local AI{f' ({model})' if model else ''}. Nothing left your computer."
        )
    return f"Written by {method}."


def when(timestamp: str, *, now: datetime | None = None) -> str:
    """ "just now", "5 min ago", "yesterday", or a date."""
    then = parse_utc(timestamp)
    current = now or datetime.now(UTC)
    seconds = max(int((current - then).total_seconds()), 0)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return plural(seconds // 3600, "hour") + " ago"
    if seconds < 172800:
        return "yesterday"
    if seconds < 86400 * 14:
        return f"{seconds // 86400} days ago"
    return then.astimezone().strftime("%b %d")


def test_summary(run: TestRun | None) -> tuple[str, bool | None]:
    """(sentence, passed?) for the last test run. ``passed`` is None when unknown."""
    if run is None:
        return "No tests run yet", None
    failed, passed = run.failed, run.passed
    if failed:
        return f"{plural(failed, 'test')} failing", False
    if run.exit_code != 0:
        return "The last run didn't finish cleanly", False
    if passed:
        return f"All {plural(passed, 'test')} passing", True
    return "The last run finished without errors", True
