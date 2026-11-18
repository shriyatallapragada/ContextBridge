"""Deterministic project momentum: `git log` + `git diff --stat`, parsed with no model."""

from __future__ import annotations

import subprocess
from pathlib import Path

from contextbridge.core.git_history import (
    DEFAULT_LIMIT,
    load_git_history,
    momentum_summary,
    parse_diff_stat,
    parse_git_log,
    read_git_history,
    sync_git_history,
)
from contextbridge.storage.db import Database
from tests.helpers import commit_all, write

LOG_SAMPLE = (
    "\x1e".join(
        [
            "abc123def456\x1fabc1234\x1fAda\x1f2026-11-18T13:37:00+05:30\x1fAdd provider layer\x1f",
            "fff222333444\x1f fff2223\x1fBob\x1f2026-11-17T09:00:00Z\x1fInitial commit\x1f0000000",
        ]
    )
    + "\x1e"
)

STAT_SAMPLE = """\
 contextbridge/service.py | 12 ++++++----
 contextbridge/main.py    |  3 ++-
 2 files changed, 9 insertions(+), 6 deletions(-)
"""


def test_parse_git_log_splits_records_and_normalises_time() -> None:
    entries = parse_git_log(LOG_SAMPLE)
    assert [e.short_sha for e in entries] == ["abc1234", "fff2223"]
    assert entries[0].author == "Ada"
    assert entries[0].subject == "Add provider layer"
    # The +05:30 commit is normalised to UTC so stored rows sort chronologically.
    assert entries[0].committed_at == "2026-11-18T08:07:00Z"
    assert entries[0].parents == []
    assert entries[1].parents == ["0000000"]


def test_parse_git_log_ignores_incomplete_records() -> None:
    assert parse_git_log("") == []
    assert parse_git_log("only\x1ftwo\x1ffields") == []


def test_parse_diff_stat_reads_paths_totals_and_renames() -> None:
    files, insertions, deletions = parse_diff_stat(STAT_SAMPLE)
    assert [f.path for f in files] == ["contextbridge/service.py", "contextbridge/main.py"]
    assert files[0].lines == 12
    assert (insertions, deletions) == (9, 6)


def test_parse_diff_stat_handles_binary_and_rename_notation() -> None:
    files, insertions, deletions = parse_diff_stat(
        " assets/logo.png | Bin 0 -> 1024 bytes\n"
        " {old.py => new.py} | 4 ++++\n"
        " 2 files changed, 4 insertions(+), 0 deletions(-)\n"
    )
    assert [f.path for f in files] == ["assets/logo.png", "new.py"]
    assert files[0].lines == 0
    assert files[1].lines == 4
    assert (insertions, deletions) == (4, 0)


def test_read_history_returns_the_last_fifteen_commits(git_repo: Path) -> None:
    for index in range(17):
        write(git_repo, f"file{index}.txt", f"{index}\n")
        commit_all(git_repo, f"commit {index}")

    commits = read_git_history(git_repo)

    assert len(commits) == DEFAULT_LIMIT
    assert commits[0].subject == "commit 16"
    assert commits[-1].subject == "commit 2"
    newest, oldest = commits[0], commits[-1]
    assert newest.committed_at >= oldest.committed_at
    assert newest.files[0].path == "file16.txt"
    assert newest.files[0].lines == 1
    assert newest.insertions == 1 and newest.deletions == 0
    assert all(c.sha and c.short_sha and c.author for c in commits)


def test_root_commits_and_empty_repositories_are_handled(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert read_git_history(empty) == []

    # A repository whose first commit adds a file still gets a diff for the root commit.
    repo = tmp_path / "rooted"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    (repo / "first.txt").write_text("one\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", "root"]):
        subprocess.run(
            ["git", "-c", "user.email=a@b.c", "-c", "user.name=A", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    commits = read_git_history(repo)

    assert len(commits) == 1
    assert commits[0].subject == "root"
    assert [f.path for f in commits[0].files] == ["first.txt"]
    assert commits[0].insertions == 1


def test_history_is_reproducible_and_persisted(git_repo: Path) -> None:
    first = read_git_history(git_repo)
    second = read_git_history(git_repo)
    assert [c.to_dict() for c in first] == [c.to_dict() for c in second]

    with Database.open(git_repo.parent / "momentum.db") as db:
        stored = sync_git_history(db, git_repo)
        assert [c.sha for c in stored] == [c.sha for c in first]
        assert [c.sha for c in db.list_git_commits()] == [c.sha for c in first]
        # An empty database refreshes itself on load; a stored one is reused.
        assert len(load_git_history(db, git_repo)) == len(first)
        assert len(load_git_history(db, git_repo, refresh=True)) == len(first)


def test_momentum_summary_counts_the_window(git_repo: Path) -> None:
    write(git_repo, "a.py", "print(1)\n")
    commit_all(git_repo, "add a")
    commits = read_git_history(git_repo, limit=5)

    summary = momentum_summary(commits)

    assert summary["commits"] == 2
    assert summary["window"] == 2
    assert summary["files_touched"] == 2  # README.md from the fixture plus a.py
    assert summary["insertions"] == 2
    assert summary["authors"] == ["Test"]
    assert summary["latest_at"] == commits[0].committed_at
    assert summary["oldest_at"] == commits[-1].committed_at


def test_non_repository_returns_no_history(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert read_git_history(plain) == []
