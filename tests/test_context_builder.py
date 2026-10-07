from __future__ import annotations

from pathlib import Path

import pytest

from contextbridge.config import Config
from contextbridge.core.git_observer import GitObserver
from contextbridge.storage.db import Database, FileChange
from contextbridge.synthesis.context_builder import (
    P_ARCHITECTURE,
    P_BLOCKER,
    P_DIFF,
    P_NOTE,
    ContextItem,
    build_context,
    fit_items,
    merge_file_changes,
)
from contextbridge.synthesis.token_counter import count_tokens
from tests.helpers import commit_all, write


def _fc(
    path: str, change: str, ts: str, symbols: list[dict[str, str]], sha: str | None = "s"
) -> FileChange:
    return FileChange("id", "sess", sha, path, change, symbols, f"@@ {path} {ts}", ts)


def _sym(name: str, change: str) -> dict[str, str]:
    return {"name": name, "kind": "function", "change": change, "signature": f"def {name}()"}


def test_merge_file_changes_folds_history() -> None:
    files = merge_file_changes(
        [
            _fc("a.py", "added", "2026-01-01T00:00:01Z", [_sym("f", "added"), _sym("g", "added")]),
            _fc(
                "a.py",
                "modified",
                "2026-01-01T00:00:02Z",
                [_sym("f", "modified"), _sym("g", "removed")],
            ),
            _fc("b.py", "modified", "2026-01-01T00:00:03Z", [_sym("h", "modified")], sha=None),
        ]
    )
    assert [f.path for f in files] == ["b.py", "a.py"]  # most recent first
    a = files[1]
    assert a.change_type == "added"  # added then modified is still new in this session
    assert [(s["name"], s["change"]) for s in a.symbols] == [("f", "added")]  # g came and went
    assert a.commits == ["s", "s"] and not a.uncommitted
    assert a.snippets[0].endswith("02Z")  # newest snippet first
    assert files[0].uncommitted


def test_fit_items_cuts_lowest_priority_first() -> None:
    items = [
        ContextItem(P_NOTE, "note " * 50),
        ContextItem(P_DIFF, "diff " * 200),
        ContextItem(P_BLOCKER, "blocker"),
        ContextItem(P_ARCHITECTURE, "architecture"),
    ]
    kept = fit_items(items, 120)
    priorities = [i.priority for i in kept]
    assert priorities[:2] == [P_BLOCKER, P_ARCHITECTURE]
    assert P_NOTE not in priorities  # lowest priority dropped
    assert "truncated" in kept[-1].text  # the diff that did not fit was cut, not dropped
    assert sum(i.tokens for i in kept) <= 120


@pytest.fixture
def repo_ctx(git_repo: Path) -> tuple[Database, GitObserver, Path]:
    db = Database.open(git_repo / "state.db")
    return db, GitObserver.open(git_repo), git_repo


def test_build_context_includes_worktree_and_untracked(
    repo_ctx: tuple[Database, GitObserver, Path],
) -> None:
    db, observer, repo = repo_ctx
    write(repo, ".gitignore", "state.db*\n")
    write(repo, "svc.py", "def a():\n    return 1\n")
    commit_all(repo)
    session = db.create_session("main")
    write(repo, "svc.py", "def a():\n    return 2\n")  # uncommitted edit
    write(repo, "new_mod.py", "def brand_new():\n    pass\n")  # untracked
    write(repo, ".env", "SECRET=1\n")  # sensitive, untracked
    write(repo, "img.bin", b"\x00\x01\x02")  # binary, untracked
    ctx = build_context(db, session, Config(), project="p", observer=observer)
    by_path = {f.path: f for f in ctx.files}
    assert set(by_path) == {"svc.py", "new_mod.py"}
    assert by_path["svc.py"].uncommitted and by_path["svc.py"].symbols[0]["name"] == "a"
    assert by_path["new_mod.py"].change_type == "added"
    assert by_path["new_mod.py"].symbols[0]["name"] == "brand_new"
    text = ctx.render()
    assert "Uncommitted changes in 2 file(s)" in text
    assert "SECRET" not in text


def test_worktree_secrets_are_sanitized(repo_ctx: tuple[Database, GitObserver, Path]) -> None:
    db, observer, repo = repo_ctx
    write(repo, ".gitignore", "state.db*\n")
    commit_all(repo)
    session = db.create_session("main")
    write(repo, "settings.py", 'TOKEN = "ghp_' + "a1B2c3D4e5" * 4 + '"\n')
    ctx = build_context(db, session, Config(), project="p", observer=observer)
    assert "ghp_" not in ctx.render()
    assert "[REDACTED" in ctx.render()


def test_session_commits_are_exact(repo_ctx: tuple[Database, GitObserver, Path]) -> None:
    """Commits made before the session (same second) are not listed once the hook recorded."""
    db, observer, repo = repo_ctx
    write(repo, ".gitignore", "state.db*\n")
    commit_all(repo, "before session")
    session = db.create_session("main")
    write(repo, "a.py", "x = 1\n")
    sha = commit_all(repo, "during session")
    db.add_file_change(session.id, "a.py", "added", commit_sha=sha)
    write(repo, "b.py", "y = 1\n")
    later = commit_all(repo, "not recorded yet")
    ctx = build_context(
        db, session, Config(), project="p", observer=observer, include_worktree=False
    )
    assert [c.subject for c in ctx.commits] == ["not recorded yet", "during session"]
    assert ctx.commits[0].sha == later


def test_max_files_per_handoff(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "s.db")
    session = db.create_session("main")
    for i in range(30):
        db.add_file_change(session.id, f"f{i}.py", "modified", commit_sha=f"c{i}")
    cfg = Config()
    cfg.capture.max_files_per_handoff = 5
    ctx = build_context(db, session, cfg, project="p", observer=None)
    assert len(ctx.files) == 5 and ctx.omitted_files == 25
    assert "... and 25 more file(s)" in ctx.render()


def test_raw_tokens_counted_before_truncation(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "s.db")
    session = db.create_session("main")
    for i in range(20):
        db.add_file_change(
            session.id, f"f{i}.py", "modified", commit_sha=f"c{i}", diff_snippet="+x = 1\n" * 300
        )
    ctx = build_context(db, session, Config(), project="p", observer=None)
    capped = ctx.render(2000)
    assert count_tokens(capped) <= 2000
    assert ctx.raw_tokens == count_tokens(ctx.render()) > 2000
