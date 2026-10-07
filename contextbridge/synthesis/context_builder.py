"""Assemble a session's raw context and fit it to a token budget.

Sources: the session's decision logs, recorded file changes (merged per file, with
symbols), the latest test run, the branch and commits since the session started, and
live uncommitted changes. Everything is already sanitized in the database; live diffs
and commit subjects are sanitized here.

When over budget, content is cut by priority (lowest first): blockers, architecture
decisions, failing tests and bugs, changed symbols, todos, diff snippets, notes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from contextbridge.config import Config
from contextbridge.core.ast_parser import symbols_for_change
from contextbridge.core.git_observer import CommitInfo, GitObserver
from contextbridge.core.sanitizer import sanitize
from contextbridge.errors import ContextBridgeError
from contextbridge.storage.db import Database, Decision, FileChange, Session, TestRun, parse_utc
from contextbridge.synthesis.token_counter import count_tokens, truncate_to_tokens

log = logging.getLogger("contextbridge")

MAX_COMMITS = 30
MAX_SYMBOLS_LISTED = 12

# Priorities: lower is more important and is cut last.
P_HEADER = 0
P_BLOCKER = 1
P_ARCHITECTURE = 2
P_FAILING = 3  # failing tests and `bug` logs
P_SYMBOLS = 4
P_TODO = 5
P_DIFF = 6
P_NOTE = 7

SECTION_TITLES: dict[int, str] = {
    P_HEADER: "Session",
    P_BLOCKER: "Blockers",
    P_ARCHITECTURE: "Architecture decisions",
    P_FAILING: "Failing tests and bugs",
    P_SYMBOLS: "Changed files and symbols",
    P_TODO: "Todos",
    P_DIFF: "Diff snippets",
    P_NOTE: "Notes",
}
_TAG_PRIORITY = {
    "blocker": P_BLOCKER,
    "architecture": P_ARCHITECTURE,
    "bug": P_FAILING,
    "todo": P_TODO,
    "note": P_NOTE,
}
TRUNCATED_MARKER = "\n[... truncated ...]"


@dataclass
class FileSummary:
    """All changes to one file in the session, merged (oldest to newest)."""

    path: str
    change_type: str
    symbols: list[dict[str, str]] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    snippets: list[str] = field(default_factory=list)  # newest first
    uncommitted: bool = False
    last_changed: str = ""


@dataclass
class ContextItem:
    priority: int
    text: str
    tokens: int = 0

    def __post_init__(self) -> None:
        self.tokens = count_tokens(self.text)


@dataclass
class SessionContext:
    project: str
    branch: str
    session: Session
    commits: list[CommitInfo]
    decisions: list[Decision]  # newest first
    files: list[FileSummary]  # most recently changed first, capped
    omitted_files: int
    test_run: TestRun | None
    items: list[ContextItem]
    raw_tokens: int = 0

    def __post_init__(self) -> None:
        self.raw_tokens = count_tokens(render_items(self.items))

    def decisions_tagged(self, tag: str) -> list[Decision]:
        return [d for d in self.decisions if d.tag == tag]

    @property
    def tests_failing(self) -> bool:
        run = self.test_run
        return run is not None and (run.exit_code != 0 or bool(run.failed))

    def render(self, max_tokens: int | None = None) -> str:
        """The context as text, cut by priority to fit ``max_tokens``."""
        if max_tokens is None:
            return render_items(self.items)
        return render_items(fit_items(self.items, max_tokens), max_tokens=max_tokens)


# ----- rendering and fitting ----------------------------------------------------------


def render_items(items: list[ContextItem], *, max_tokens: int | None = None) -> str:
    """Render items grouped into sections in priority order."""
    parts: list[str] = []
    for priority in sorted(SECTION_TITLES):
        texts = [i.text for i in items if i.priority == priority]
        if texts:
            parts.append(f"## {SECTION_TITLES[priority]}\n" + "\n".join(texts))
    text = "\n\n".join(parts)
    if max_tokens is not None:
        text = truncate_to_tokens(text, max_tokens, marker=TRUNCATED_MARKER)
    return text


def _section_overhead(items: list[ContextItem]) -> int:
    priorities = {i.priority for i in items}
    return sum(count_tokens(f"## {SECTION_TITLES[p]}\n\n\n") for p in priorities)


def fit_items(items: list[ContextItem], max_tokens: int) -> list[ContextItem]:
    """Keep items in priority order until the budget runs out.

    Everything of a higher priority is kept in full before anything lower is
    considered. The first item that does not fit is truncated (if a useful amount of
    budget is left) and everything after it is dropped.
    """
    ordered = sorted(items, key=lambda i: i.priority)  # stable: keeps recency order
    budget = max_tokens - _section_overhead(ordered)
    kept: list[ContextItem] = []
    for item in ordered:
        cost = item.tokens + 1  # newline joining items
        if cost <= budget:
            kept.append(item)
            budget -= cost
            continue
        if budget > 40:
            cut = truncate_to_tokens(item.text, budget - 1, marker=TRUNCATED_MARKER)
            if cut:
                kept.append(ContextItem(item.priority, cut))
        break
    return kept


# ----- collection ------------------------------------------------------------------------


_CHANGE_RANK = {"added": 0, "modified": 1, "removed": 2}


def _merge_symbols(
    current: dict[tuple[str, str], dict[str, str]], new: list[dict[str, Any]]
) -> None:
    """Fold one change's symbols into ``current`` (applied oldest to newest)."""
    for sym in new:
        key = (str(sym.get("name", "")), str(sym.get("kind", "")))
        change = str(sym.get("change", "modified"))
        previous = current.get(key)
        if previous is not None and previous["change"] == "added":
            if change == "removed":
                del current[key]  # added and removed again within the session
                continue
            change = "added"
        current[key] = {
            "name": key[0],
            "kind": key[1],
            "change": change,
            "signature": str(sym.get("signature", "")),
        }


def merge_file_changes(changes: list[FileChange]) -> list[FileSummary]:
    """Merge per-commit rows into one summary per file, most recently changed first."""
    by_path: dict[str, FileSummary] = {}
    symbols: dict[str, dict[tuple[str, str], dict[str, str]]] = {}
    for change in sorted(changes, key=lambda c: c.timestamp):
        summary = by_path.get(change.file_path)
        if summary is None:
            summary = by_path[change.file_path] = FileSummary(
                path=change.file_path, change_type=change.change_type
            )
            symbols[change.file_path] = {}
        elif change.change_type == "deleted" or summary.change_type != "added":
            summary.change_type = change.change_type
        _merge_symbols(symbols[change.file_path], change.symbols)
        if change.commit_sha:
            summary.commits.append(change.commit_sha)
        else:
            summary.uncommitted = True
        if change.diff_snippet:
            summary.snippets.insert(0, change.diff_snippet)
        summary.last_changed = change.timestamp
    for path, summary in by_path.items():
        summary.symbols = sorted(
            symbols[path].values(), key=lambda s: (_CHANGE_RANK.get(s["change"], 3), s["name"])
        )
    return sorted(by_path.values(), key=lambda f: f.last_changed, reverse=True)


def _worktree_changes(observer: GitObserver, session_id: str, config: Config) -> list[FileChange]:
    """Uncommitted changes vs HEAD, as unsaved FileChange rows (sanitized)."""
    capture = config.capture
    result = observer.diff(
        "HEAD",
        None,
        ignore_globs=capture.ignore_globs,
        max_diff_chars=capture.max_diff_chars_per_file,
    )
    untracked = observer.untracked_changes(
        ignore_globs=capture.ignore_globs, max_diff_chars=capture.max_diff_chars_per_file
    )
    result.files += untracked.files
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for change in result.files:
        old = None
        if change.change_type.value != "added":
            old = observer.file_at("HEAD", change.old_path or change.path)
        new = None if change.change_type.value == "deleted" else observer.file_at(None, change.path)
        symbols = symbols_for_change(
            change.path, old, new, change.patch, change_type=change.change_type.value
        )
        rows.append(
            FileChange(
                id="worktree",
                session_id=session_id,
                commit_sha=None,
                file_path=sanitize(change.path),
                change_type=change.change_type.value,
                symbols=[{k: sanitize(v) for k, v in s.items()} for s in symbols],
                diff_snippet=sanitize(change.snippet),
                timestamp=now,
            )
        )
    return rows


def _session_commits(db: Database, observer: GitObserver, session: Session) -> list[CommitInfo]:
    """Commits of this session, newest first.

    Uses the commits the hook recorded (exact), plus any made after the last recorded
    one (the hook may still be running, or was skipped). Without recorded commits
    (e.g. `--no-hooks`), falls back to commit time; git's one-second resolution can
    then include a commit made just before the session started.
    """
    recorded = db.session_commit_shas(session.id)
    if not recorded:
        return observer.commits_since(session.start_time, limit=MAX_COMMITS)
    newer = observer.commits_since(session.start_time, exclude=recorded[-1], limit=MAX_COMMITS)
    known = {c.sha for c in newer}
    older = observer.commit_infos([sha for sha in reversed(recorded) if sha not in known])
    return (newer + older)[:MAX_COMMITS]


def _age(timestamp: str) -> str:
    seconds = max(int((datetime.now(UTC) - parse_utc(timestamp)).total_seconds()), 0)
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600}h"


def format_symbols(symbols: list[dict[str, str]], limit: int = MAX_SYMBOLS_LISTED) -> str:
    shown = [
        f"{s['name']} ({s['change']})" if s["change"] != "modified" else s["name"]
        for s in symbols[:limit]
    ]
    more = len(symbols) - limit
    return ", ".join(shown) + (f" (+{more} more)" if more > 0 else "")


def describe_test_run(run: TestRun) -> str:
    counts = (
        f"{run.passed if run.passed is not None else '?'} passed, "
        f"{run.failed if run.failed is not None else '?'} failed, "
        f"{run.skipped if run.skipped is not None else '?'} skipped"
    )
    if run.passed is None and run.failed is None:
        counts = "no test counts"
    return f"`{run.command}` exited {run.exit_code} ({counts})"


def _build_items(
    project: str,
    branch: str,
    session: Session,
    commits: list[CommitInfo],
    decisions: list[Decision],
    files: list[FileSummary],
    omitted_files: int,
    test_run: TestRun | None,
) -> list[ContextItem]:
    header = [
        f"Project: {project}",
        f"Branch: {branch}",
        f"Session {session.id}, started {_age(session.start_time)} ago",
    ]
    if commits:
        header.append(f"Commits this session ({len(commits)}, newest first):")
        header += [f"- {c.sha[:8]} {c.subject}" for c in commits]
    else:
        header.append("Commits this session: none")
    uncommitted = sum(f.uncommitted for f in files)
    if uncommitted:
        header.append(f"Uncommitted changes in {uncommitted} file(s)")
    items = [ContextItem(P_HEADER, "\n".join(header))]

    # Decisions: blockers, then architecture, ... (newest first within each tag).
    for d in decisions:
        items.append(ContextItem(_TAG_PRIORITY.get(d.tag, P_NOTE), f"- [{d.tag}] {d.content}"))

    if test_run is not None:
        lines = [f"Last test run: {describe_test_run(test_run)}"]
        for failure in test_run.failures:
            message = failure.get("message", "")
            lines.append(f"- {failure.get('test', '?')}" + (f": {message}" if message else ""))
        items.append(ContextItem(P_FAILING, "\n".join(lines)))

    for f in files:
        flags = [f.change_type]
        if f.commits:
            flags.append(f"{len(f.commits)} commit(s)")
        if f.uncommitted:
            flags.append("uncommitted")
        line = f"- {f.path} ({', '.join(flags)})"
        if f.symbols:
            line += f": {format_symbols(f.symbols)}"
        items.append(ContextItem(P_SYMBOLS, line))
    if omitted_files:
        items.append(ContextItem(P_SYMBOLS, f"- ... and {omitted_files} more file(s)"))

    for f in files:
        if f.snippets:
            body = "\n".join(f.snippets)
            items.append(ContextItem(P_DIFF, f"### {f.path}\n```diff\n{body}\n```"))
    return items


def build_context(
    db: Database,
    session: Session,
    config: Config,
    *,
    project: str,
    observer: GitObserver | None,
    include_worktree: bool = True,
) -> SessionContext:
    """Collect everything known about ``session`` into a SessionContext."""
    decisions = db.list_decisions(session.id)
    changes = db.list_file_changes(session.id)
    commits: list[CommitInfo] = []
    branch = session.branch_name
    if observer is not None:
        try:
            state = observer.repo_state()
            branch = state.branch or (
                f"detached at {state.head_sha[:8]}" if state.head_sha else branch
            )
            commits = _session_commits(db, observer, session)
            if include_worktree and state.has_commits:
                changes = changes + _worktree_changes(observer, session.id, config)
        except ContextBridgeError as exc:
            log.warning("context: git unavailable: %s", exc.message)
    commits = [
        CommitInfo(sha=c.sha, committed_at=c.committed_at, subject=sanitize(c.subject))
        for c in commits
    ]
    all_files = merge_file_changes(changes)
    limit = config.capture.max_files_per_handoff
    files, omitted = all_files[:limit], max(len(all_files) - limit, 0)
    test_run = db.last_test_run(session.id)
    items = _build_items(
        project, sanitize(branch), session, commits, decisions, files, omitted, test_run
    )
    return SessionContext(
        project=project,
        branch=sanitize(branch),
        session=session,
        commits=commits,
        decisions=decisions,
        files=files,
        omitted_files=omitted,
        test_run=test_run,
        items=items,
    )
