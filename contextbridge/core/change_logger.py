"""Record Git changes and symbols independently of any front end."""

from __future__ import annotations

import logging

from contextbridge.config import Config
from contextbridge.core.git_observer import GitObserver
from contextbridge.core.watcher import excluded, ignored_by_gitignore
from contextbridge.storage.db import Database
from contextbridge.storage.workspace import Workspace

# Older unrecorded commits of the session picked up by each hook run (e.g. after a
# rebase, which does not run post-commit for every pick).
BACKFILL_LIMIT = 20

log = logging.getLogger("contextbridge")


def record_commit(
    db: Database, observer: GitObserver, session_id: str, sha: str, config: Config
) -> int:
    """Store metadata and numeric summaries, never source text or symbol signatures."""
    if db.has_commit(sha):
        return 0
    parent = observer.parent_of(sha)
    files = [
        change
        for change in observer.numeric_changes(parent, sha)
        if change.snippet != "binary file changed"
        and not excluded(change.path, config.capture.ignore_globs)
        and not ignored_by_gitignore(observer.root, change.path)
        and not (change.old_path and excluded(change.old_path, config.capture.ignore_globs))
    ]
    return db.add_commit_changes(session_id, sha, files)


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
