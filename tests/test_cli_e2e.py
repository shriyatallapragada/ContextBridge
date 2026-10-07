"""End-to-end: init -> log -> commit -> run -> handoff in a temp repo, as real processes.

Deterministic path only: no LLM, no network (Ollama points at a closed port and the
tokenizer is the offline estimate).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers import commit_all, git, write

MODULE = [sys.executable, "-m", "contextbridge.main"]

CART = """\
class Cart:
    def total(self):
        return sum(self.items)

    def add(self, item):
        self.items.append(item)
"""

TESTS = """\
def test_adds_items():
    assert 1 + 1 == 2


def test_empty_cart_total():
    assert 0 == 1, "empty cart total should be zero"
"""


@pytest.fixture(autouse=True)
def _offline_tokenizer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CB_TOKENIZER", "estimate")


def cb(repo: Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [*MODULE, *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**os.environ, "COLUMNS": "200"},  # no terminal: keep Rich from wrapping at 80
    )
    assert proc.returncode == expect, (
        f"cb {' '.join(args)} exited {proc.returncode}, expected {expect}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc


def state(repo: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads((repo / ".context" / "state.json").read_text())
    return data


@pytest.fixture
def project(git_repo: Path) -> Path:
    """A repo with a prior commit, initialized, with a session's worth of activity."""
    write(git_repo, "cart.py", CART)
    write(git_repo, "CLAUDE.md", "# Team rules\n\n- keep functions small\n")
    commit_all(git_repo, "add cart")
    cb(git_repo, "init", "--no-hooks")
    commit_all(git_repo, "ignore .context/")
    cb(git_repo, "log", "Prices are stored as integer cents", "--tag", "architecture")
    cb(git_repo, "log", "Empty cart crashes checkout", "--tag", "blocker")
    cb(git_repo, "log", "Add discount codes", "--tag", "todo")
    write(git_repo, "cart.py", CART.replace("sum(self.items)", "sum(i.cents for i in self.items)"))
    commit_all(git_repo, "use integer cents")
    cb(git_repo, "hook", "post-commit")
    write(git_repo, "tests/test_cart.py", TESTS)
    cb(
        git_repo,
        "run",
        "--",
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests",
        expect=1,
    )
    return git_repo


def test_full_workflow(project: Path) -> None:
    repo = project
    original_claude_md = (repo / "CLAUDE.md").read_text()

    # status reflects everything captured so far
    status = cb(repo, "status").stdout
    for needle in ("integer cents", "Empty cart crashes", "Cart.total", "1 passed, 1 failed"):
        assert needle in status, needle

    # a dry run previews and writes nothing
    dry = cb(repo, "handoff", "--dry-run", "--from", "claude", "--to", "cursor")
    assert "nothing written" in dry.stdout.replace("\n", " ")
    assert not (repo / ".cursor").exists() and state(repo)["last_handoff"] is None

    # hand off to every adapter, twice
    expected = {
        "claude": repo / "CLAUDE.md",
        "cursor": repo / ".cursor" / "rules" / "handoff.mdc",
        "generic": repo / "HANDOFF.md",
    }
    for _ in range(2):
        for agent, path in expected.items():
            cb(
                repo,
                "handoff",
                "--from",
                "claude" if agent != "claude" else "cursor",
                "--to",
                agent,
            )
            assert path.is_file(), agent
    for agent, path in expected.items():
        text = path.read_text()
        assert text.count("<!-- contextbridge:start -->") == 1, agent
        for needle in ("integer cents", "Empty cart crashes checkout", "Add discount codes"):
            assert needle in text, (agent, needle)
        assert "tests/test_cart.py::test_empty_cart_total" in text, agent
        assert "Cart.total" in text, agent
        assert "context_handoff" not in text or agent == "claude"
    claude_text = expected["claude"].read_text()
    assert claude_text.startswith(original_claude_md)  # user rules untouched, block appended
    assert claude_text.count("<context_handoff>") == 1

    # bookkeeping: handoffs table and last_handoff
    last = state(repo)["last_handoff"]
    assert isinstance(last, dict) and last["to"] == "generic" and last["method"] == "deterministic"
    conn = sqlite3.connect(repo / ".context" / "state.db")
    try:
        db = conn.execute("SELECT to_agent, written_path FROM handoffs").fetchall()
    finally:
        conn.close()
    assert len(db) == 6 and {r[0] for r in db} == {"claude", "cursor", "generic"}

    # injected files never feed the next handoff
    again = cb(repo, "handoff", "--dry-run", "--print", "--no-llm", "--to", "claude").stdout
    assert "contextbridge:" not in again and "context_handoff" not in again

    # clean up: the user's CLAUDE.md is exactly as it was
    cb(repo, "clear", "--cleanup-adapters")
    assert (repo / "CLAUDE.md").read_text() == original_claude_md
    assert not (repo / "HANDOFF.md").exists() and not (repo / ".cursor").exists()
    assert git(repo, "status", "--porcelain").count("CLAUDE.md") == 0

    # archive, then wipe
    cb(repo, "clear", "--session")
    cb(repo, "log", "new session note")
    cb(repo, "clear", "--all", "--yes")
    assert state(repo)["last_handoff"] is None
    cb(repo, "log", "workspace still works")


def test_exit_codes(git_repo: Path, tmp_path: Path) -> None:
    cb(git_repo, "status", expect=2)  # not initialized
    cb(git_repo, "handoff", "--dry-run", expect=2)
    cb(git_repo, "init", "--no-hooks")
    cb(git_repo, "handoff", "--from", "claude", "--to", "gemini", expect=1)  # unknown agent
    cb(git_repo, "handoff", "--to", "cursor", expect=1)  # missing --from
    cb(git_repo, "log", "", expect=1)
    cb(git_repo, "clear", "--session", "--all", expect=1)


def test_works_without_git_hooks_or_llm(project: Path) -> None:
    """The 5-minute path: nothing but git and cb. No LLM, hooks off, no network."""
    proc = cb(project, "handoff", "--from", "claude", "--to", "cursor")
    assert "deterministic" in proc.stdout
    assert "Raw context" in proc.stdout and "Compression" in proc.stdout
