"""`cb hook post-commit`: record the latest commit's file changes and symbols.

Runs from the git hook in the background. It is silent and never fails loudly:
every error is logged to `.context/cb.log` and the process exits 0.
"""

from __future__ import annotations

import contextlib

import typer

from contextbridge.cli.common import app_context, log
from contextbridge.config import Config
from contextbridge.core.ast_parser import symbols_for_change
from contextbridge.core.git_observer import ChangeType, FileDiff, GitObserver
from contextbridge.errors import ContextBridgeError
from contextbridge.storage.db import Database
from contextbridge.storage.workspace import Workspace

# Older unrecorded commits of the session picked up by each hook run (e.g. after a
# rebase, which does not run post-commit for every pick).
BACKFILL_LIMIT = 20

hook_app = typer.Typer(
    help="Internal: entry points called by git hooks.", no_args_is_help=True, hidden=True
)


def _sources(
    observer: GitObserver, sha: str, parent: str, change: FileDiff
) -> tuple[str | None, str | None]:
    old_path = change.old_path or change.path
    old = None if change.change_type == ChangeType.added else observer.file_at(parent, old_path)
    new = None if change.change_type == ChangeType.deleted else observer.file_at(sha, change.path)
    return old, new


def record_commit(
    db: Database, observer: GitObserver, session_id: str, sha: str, config: Config
) -> int:
    """Store one row per changed file of ``sha``. Returns the number of rows added."""
    if db.has_commit(sha):
        return 0
    parent = observer.parent_of(sha)
    result = observer.diff(
        parent,
        sha,
        ignore_globs=config.capture.ignore_globs,
        max_diff_chars=config.capture.max_diff_chars_per_file,
    )
    for skipped in result.skipped:
        log.debug("hook: skipped %s (%s) in %s", skipped.path, skipped.reason, sha[:12])
    added = 0
    for change in result.files:
        old, new = _sources(observer, sha, parent, change)
        symbols = symbols_for_change(change.path, old, new, change.patch)
        row = db.add_file_change(
            session_id,
            change.path,
            change.change_type.value,
            commit_sha=sha,
            symbols=symbols,
            diff_snippet=change.snippet,
        )
        added += row is not None
    return added


def ingest_head(ws: Workspace) -> int:
    """Record HEAD (and recent unrecorded session commits). Returns rows added."""
    observer = GitObserver.open(ws.root)
    repo = observer.repo_state()
    if not repo.has_commits or repo.head_sha is None:
        return 0
    if repo.operation == "rebase":
        # Commits are about to be rewritten; the next commit afterwards backfills them.
        log.info("hook: rebase in progress, skipping %s", repo.head_sha[:12])
        return 0
    config = ws.load_config()
    with ws.open_db() as db:
        session, _ = ws.active_session(db)
        pending = [repo.head_sha]
        last = db.last_commit_sha(session.id)
        if last is not None:
            # Commits since the last one we recorded (e.g. a rebase does not run the
            # hook for every pick). Never before the session started.
            missed = observer.commits_since(session.start_time, exclude=last, limit=BACKFILL_LIMIT)
            pending += [c.sha for c in missed if c.sha != repo.head_sha]
        total = 0
        # Oldest first so rows appear in commit order.
        for sha in reversed(pending):
            total += record_commit(db, observer, session.id, sha, config)
    log.info("hook: recorded %d file change(s) up to %s", total, repo.head_sha[:12])
    return total


@hook_app.command("post-commit")
def post_commit_command(ctx: typer.Context) -> None:
    """Record the latest commit. Silent; always exits 0."""
    try:
        ws = Workspace.locate(app_context(ctx).start_dir)
    except ContextBridgeError:
        return  # Not initialized here: nothing to do, and nothing to complain about.
    try:
        ws.setup_logging(verbose=app_context(ctx).verbose)
        ingest_head(ws)
    except Exception:
        with contextlib.suppress(Exception):  # Even logging must not fail the hook.
            log.exception("hook post-commit failed")
