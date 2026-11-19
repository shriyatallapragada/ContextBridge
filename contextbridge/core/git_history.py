"""Deterministic project-momentum parsing: the last N commits, straight from git.

Everything here is plain text parsing and subprocess calls. It is the only code that
reads the commit history for the dashboard, and it never costs a model call.

The window is exactly ``git log --max-count=N`` plus one ``git diff --stat`` per commit,
so the stored timeline can always be reproduced from the repository itself.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from contextbridge.core.sanitizer import sanitize

if TYPE_CHECKING:
    from contextbridge.storage.db import Database

DEFAULT_LIMIT: Final = 15
MAX_LIMIT: Final = 100
GIT_TIMEOUT: Final = 15.0
FIELD_SEP: Final = "\x1f"
RECORD_SEP: Final = "\x1e"
# The well-known hash of the empty tree; lets root commits get a `git diff --stat` too.
EMPTY_TREE: Final = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
LOG_FORMAT: Final = FIELD_SEP.join(["%H", "%h", "%an", "%cI", "%s", "%P"]) + RECORD_SEP

_INSERTIONS: Final = re.compile(r"(\d+) insertions?\(\+\)")
_DELETIONS: Final = re.compile(r"(\d+) deletions?\(-\)")
_SUMMARY: Final = re.compile(r"(\d+) files? changed")


@dataclass(frozen=True)
class ChangedFile:
    """One file in a commit's ``--stat`` output."""

    path: str
    lines: int = 0


@dataclass(frozen=True)
class CommitRecord:
    """A parsed commit ready for storage or serialization."""

    sha: str
    short_sha: str
    author: str
    committed_at: str  # UTC ISO-8601 with a Z suffix
    subject: str
    files: list[ChangedFile]
    insertions: int
    deletions: int
    parsed_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sha": self.sha,
            "short_sha": self.short_sha,
            "author": self.author,
            "committed_at": self.committed_at,
            "subject": self.subject,
            "insertions": self.insertions,
            "deletions": self.deletions,
            "files": [{"path": f.path, "lines": f.lines} for f in self.files],
        }


@dataclass(frozen=True)
class LogEntry:
    """One record from ``git log`` before its diff stats are attached."""

    sha: str
    short_sha: str
    author: str
    committed_at: str
    subject: str
    parents: list[str]


def _iso_utc(value: str) -> str:
    """Normalise git's strict ISO date to a comparable UTC ``...Z`` string."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return sanitize(value)[:40]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _run_git(root: Path, *args: str) -> str | None:
    """Run one git command; None means "no usable output" (not a repo, no commits, ...)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotePath=false", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def parse_git_log(text: str) -> list[LogEntry]:
    """Parse the record-separated ``git log`` output (pure; no git required)."""
    entries: list[LogEntry] = []
    for record in text.split(RECORD_SEP):
        record = record.strip("\n")
        if not record.strip():
            continue
        fields = record.split(FIELD_SEP)
        if len(fields) < 6:
            continue
        sha, short_sha, author, committed_at, subject, parents = fields[:6]
        if not sha.strip():
            continue
        entries.append(
            LogEntry(
                sha=sha.strip(),
                short_sha=short_sha.strip(),
                author=sanitize(author.strip())[:80] or "unknown",
                committed_at=_iso_utc(committed_at.strip()),
                subject=sanitize(subject.strip())[:300] or "(no subject)",
                parents=[p for p in parents.split() if p],
            )
        )
    return entries


def _clean_stat_path(raw: str) -> str:
    """Undo git's ``{old => new}`` rename notation in ``--stat`` rows."""
    path = raw.strip()
    if "=>" not in path:
        return path
    if "{" in path and "}" in path:
        head, _, rest = path.partition("{")
        middle, _, tail = rest.partition("}")
        _, _, after = middle.partition("=>")
        return (head + after.strip() + tail).strip()
    _, _, after = path.partition("=>")
    return after.strip()


def parse_diff_stat(text: str) -> tuple[list[ChangedFile], int, int]:
    """Parse ``git diff --stat`` into ``(files, insertions, deletions)`` (pure)."""
    files: list[ChangedFile] = []
    insertions = deletions = 0
    for line in text.splitlines():
        if "|" in line and not line.lstrip().startswith("..."):
            path_part, _, rest = line.partition("|")
            path = _clean_stat_path(path_part)
            if not path or path.startswith(" "):
                continue
            tail = rest.split()
            lines = 0
            if tail and tail[0] == "Bin":
                lines = 0
            elif tail and tail[0].isdigit():
                lines = int(tail[0])
            files.append(ChangedFile(path=sanitize(path)[:400], lines=lines))
            continue
        if _SUMMARY.search(line):
            found = _INSERTIONS.search(line)
            insertions = int(found.group(1)) if found else 0
            found = _DELETIONS.search(line)
            deletions = int(found.group(1)) if found else 0
    return files, insertions, deletions


def read_git_history(root: Path, *, limit: int = DEFAULT_LIMIT) -> list[CommitRecord]:
    """The last ``limit`` commits with their diff stats, newest first. Never raises."""
    window = max(1, min(int(limit), MAX_LIMIT))
    raw = _run_git(root, "log", f"--max-count={window}", f"--format={LOG_FORMAT}")
    if raw is None:
        return []
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    commits: list[CommitRecord] = []
    for entry in parse_git_log(raw):
        base = entry.parents[0] if entry.parents else EMPTY_TREE
        stat = _run_git(root, "diff", "--stat", "--no-ext-diff", base, entry.sha)
        files, insertions, deletions = parse_diff_stat(stat) if stat else ([], 0, 0)
        commits.append(
            CommitRecord(
                sha=entry.sha,
                short_sha=entry.short_sha,
                author=entry.author,
                committed_at=entry.committed_at,
                subject=entry.subject,
                files=files,
                insertions=insertions,
                deletions=deletions,
                parsed_at=now,
            )
        )
    # `git log` already yields newest first; re-sorting by timestamp alone would reorder
    # commits made within the same second, so the log's own order is preserved.
    return commits


def sync_git_history(db: Database, root: Path, *, limit: int = DEFAULT_LIMIT) -> list[CommitRecord]:
    """Parse the history window and persist it, replacing any previous window."""
    commits = read_git_history(root, limit=limit)
    db.replace_git_commits(commits)
    return commits


def load_git_history(
    db: Database,
    root: Path,
    *,
    limit: int = DEFAULT_LIMIT,
    refresh: bool = False,
) -> list[CommitRecord]:
    """Stored momentum, re-parsing only when asked or when nothing is stored yet."""
    window = max(1, min(int(limit), MAX_LIMIT))
    if refresh or not db.list_git_commits(limit=1):
        return sync_git_history(db, root, limit=window)
    return db.list_git_commits(limit=window)


def momentum_summary(commits: list[CommitRecord]) -> dict[str, object]:
    """Aggregate counts for the overview timeline; deterministic and token-free."""
    authors = sorted({c.author for c in commits})
    touched: set[str] = set()
    for commit in commits:
        touched.update(f.path for f in commit.files)
    return {
        "window": len(commits),
        "commits": len(commits),
        "authors": authors[:10],
        "insertions": sum(c.insertions for c in commits),
        "deletions": sum(c.deletions for c in commits),
        "files_touched": len(touched),
        "latest_at": commits[0].committed_at if commits else None,
        "oldest_at": commits[-1].committed_at if commits else None,
    }


def dump_commits(commits: list[CommitRecord]) -> str:
    """JSON rendering used by the CLI and MCP server (tests and agents read this)."""
    return json.dumps([c.to_dict() for c in commits], indent=2, ensure_ascii=False)
