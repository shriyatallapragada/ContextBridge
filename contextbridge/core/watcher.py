"""Explicit, bounded-cost polling of file metadata. Source contents are never read."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pathspec import GitIgnoreSpec

from contextbridge.core.git_observer import matches_ignore
from contextbridge.core.sanitizer import is_sensitive_file
from contextbridge.storage.workspace import Workspace

IGNORED_DIRS = frozenset(
    {
        ".git",
        ".contextbridge",
        ".context",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "build",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".next",
        ".nuxt",
        ".cache",
        ".ssh",
        ".aws",
        ".gnupg",
    }
)
IGNORED_NAMES = frozenset({"AGENTS.md", "HANDOFF.md", "CLAUDE.md", "cb.log", "handoff.mdc"})


def excluded(path: str, globs: list[str]) -> bool:
    parts = Path(path).parts
    return (
        any(part in IGNORED_DIRS or part.startswith(".contextbridge-copy-") for part in parts)
        or Path(path).name in IGNORED_NAMES
        or is_sensitive_file(path)
        or matches_ignore(path, globs)
    )


def ignored_by_gitignore(root: Path, relative: str) -> bool:
    """Check nested ignore rules for a path that may have been deleted."""
    path = root / relative
    ignored = False
    directory = root
    for part in [None, *Path(relative).parts[:-1]]:
        if part is not None:
            directory /= part
        ignore = directory / ".gitignore"
        if ignore.is_file() and not ignore.is_symlink():
            with ignore.open(encoding="utf-8", errors="replace") as stream:
                spec = GitIgnoreSpec.from_lines(stream.read(1_000_000).splitlines())
            match = spec.check_file(path.relative_to(directory).as_posix())
            if match.include is not None:
                ignored = bool(match.include)
    return ignored


@dataclass(frozen=True)
class FileStamp:
    size: int
    modified_ns: int
    changed_ns: int
    inode: int


@dataclass
class WatchMetrics:
    scans: int = 0
    events: int = 0  # observed eligible file transitions, coalesced between polls
    batches: int = 0
    files: int = 0  # sum of distinct paths in each persisted batch


class ProjectWatcher:
    """No thread or polling starts on construction. Call run() explicitly.

    Snapshots and pending batches contain metadata only. Restarting establishes a
    fresh baseline. Changes while stopped are not attributed to the new watch session.
    """

    def __init__(
        self,
        workspace: Workspace,
        *,
        interval: float = 1.0,
        debounce: float = 2.0,
        batch_window: float = 30.0,
        agent: str | None = None,
        notes: str = "",
    ) -> None:
        if interval <= 0 or debounce <= 0 or batch_window <= 0:
            raise ValueError("Watch intervals must be positive.")
        self.ws = workspace
        self.interval, self.debounce, self.batch_window = interval, debounce, batch_window
        self.agent, self.notes = agent, notes
        self.metrics = WatchMetrics()
        self.previous: dict[str, FileStamp] | None = None
        self.pending: dict[str, tuple[FileStamp | None, FileStamp | None]] = {}
        self.first_event = self.last_event = 0.0
        self.pending_events = 0
        self.globs = self.ws.load_config().capture.ignore_globs

    def scan(self) -> dict[str, FileStamp]:
        """Prune ignored trees before statting children. Reload nested ignore rules each scan."""
        result: dict[str, FileStamp] = {}
        root = self.ws.root

        def visit(directory: Path, inherited: list[tuple[Path, GitIgnoreSpec]]) -> None:
            rules = inherited
            ignore = directory / ".gitignore"
            if ignore.is_file() and not ignore.is_symlink():
                try:
                    with ignore.open(encoding="utf-8", errors="replace") as stream:
                        spec = GitIgnoreSpec.from_lines(stream.read(1_000_000).splitlines())
                    rules = [*inherited, (directory, spec)]
                except FileNotFoundError:
                    pass  # a checkout replaced the ignore file during this scan
            try:
                entries = os.scandir(directory)
            except FileNotFoundError:
                return  # checkout removed a directory while we were traversing its parent
            with entries:
                for entry in entries:
                    path = Path(entry.path)
                    relative = path.relative_to(root).as_posix()
                    if entry.is_symlink() or excluded(relative, self.globs):
                        continue
                    is_dir = entry.is_dir(follow_symlinks=False)
                    ignored = False
                    for base, spec in rules:
                        match = spec.check_file(
                            path.relative_to(base).as_posix() + ("/" if is_dir else "")
                        )
                        if match.include is not None:
                            ignored = bool(match.include)
                    if ignored:
                        continue
                    if is_dir:
                        if not (path / "pyvenv.cfg").is_file():
                            visit(path, rules)
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue  # an editor's temporary file disappeared during the scan
                        result[relative] = FileStamp(
                            info.st_size,
                            info.st_mtime_ns,
                            info.st_ctime_ns,
                            info.st_ino,
                        )

        visit(root, [])
        self.metrics.scans += 1
        return result

    def poll(self, now: float | None = None) -> str | None:
        now = time.monotonic() if now is None else now
        current = self.scan()
        if self.previous is None:
            self.previous = current
            return None
        for path in self.previous.keys() | current.keys():
            before, after = self.previous.get(path), current.get(path)
            if before == after:
                continue
            if after is None and ignored_by_gitignore(self.ws.root, path):
                continue
            if not self.pending:
                self.first_event = now
            original = self.pending[path][0] if path in self.pending else before
            self.pending[path] = (original, after)
            self.last_event = now
            self.pending_events += 1
            self.metrics.events += 1
        self.previous = current
        if self.pending and (
            now - self.last_event >= self.debounce or now - self.first_event >= self.batch_window
        ):
            return self.flush(now)
        return None

    def flush(self, now: float | None = None) -> str | None:
        if not self.pending:
            return None
        now = time.monotonic() if now is None else now
        files = []
        for path, (before, after) in sorted(self.pending.items()):
            if ignored_by_gitignore(self.ws.root, path):
                continue
            if before is None and after is None:
                continue  # a temporary file was created and deleted within this batch
            change = "added" if before is None else "deleted" if after is None else "modified"
            old_size, new_size = before.size if before else 0, after.size if after else 0
            files.append(
                {
                    "path": path,
                    "change_type": change,
                    "summary": (
                        f"{change}; {old_size} -> {new_size} bytes ({new_size - old_size:+d})"
                    ),
                }
            )
        summary = None
        if files:
            with self.ws.open_db() as db:
                session, _ = self.ws.active_session(db)
                summary = db.add_change_batch(
                    session.id,
                    files,
                    max(0, now - self.first_event),
                    self.pending_events,
                    self.agent,
                    self.notes,
                )
            self.metrics.batches += 1
            self.metrics.files += len(files)
        self.pending.clear()
        self.pending_events = 0
        return summary

    def run(
        self,
        stop: threading.Event,
        *,
        on_batch: Callable[[str], None] | None = None,
        ready: threading.Event | None = None,
        seconds: float | None = None,
    ) -> WatchMetrics:
        self.poll()
        if ready is not None:
            ready.set()
        started = time.monotonic()
        try:
            while not stop.wait(self.interval):
                summary = self.poll()
                if summary and on_batch:
                    on_batch(summary)
                if seconds is not None and time.monotonic() - started >= seconds:
                    break
        finally:
            # Collect the last save before an explicit stop, then commit the pending batch.
            summary = self.poll()
            if summary and on_batch:
                on_batch(summary)
            summary = self.flush()
            if summary and on_batch:
                on_batch(summary)
        return self.metrics
