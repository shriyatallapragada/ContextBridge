"""CLI tests for Phase 4: `cb handoff` (real writes), `cb clear`, and the loop guard."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from contextbridge.adapters.base_adapter import MARKER_START
from contextbridge.main import app
from tests.helpers import commit_all, write

runner = CliRunner()


def cb(*args: str, input: str | None = None) -> tuple[int, str]:
    result = runner.invoke(app, list(args), input=input)
    return result.exit_code, result.output


def _state(repo: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((repo / ".context" / "state.json").read_text())
    return data


def _rows(repo: Path, sql: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(repo / ".context" / "state.db")
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


@pytest.fixture
def repo(git_repo: Path) -> Path:
    write(git_repo, "cart.py", "def total(items):\n    return sum(items)\n")
    commit_all(git_repo, "add cart")
    assert cb("init", "--no-hooks")[0] == 0
    commit_all(git_repo, "ignore .context/")
    cb("log", "Prices are integer cents", "--tag", "architecture")
    cb("log", "Empty cart crashes checkout", "--tag", "blocker")
    write(git_repo, "cart.py", "def total(items):\n    return sum(i.cents for i in items)\n")
    commit_all(git_repo, "integer cents")
    cb("hook", "post-commit")
    return git_repo


# ----- acceptance: each adapter writes the right file ---------------------------------------


@pytest.mark.parametrize(
    ("agent", "relative"),
    [
        ("claude", "CLAUDE.md"),
        ("claude-code", "CLAUDE.md"),
        ("cursor", ".cursor/rules/handoff.mdc"),
        ("generic", "HANDOFF.md"),
        ("aider", "HANDOFF.md"),
        ("opencode", "HANDOFF.md"),
    ],
)
def test_acceptance_handoff_writes_the_right_file(repo: Path, agent: str, relative: str) -> None:
    code, out = cb("handoff", "--from", "cursor", "--to", agent)
    assert code == 0, out
    target = repo / relative
    assert target.is_file()
    text = target.read_text()
    assert MARKER_START in text
    assert "Prices are integer cents" in text and "Empty cart crashes checkout" in text
    assert "cart.py" in text and "total" in text
    others = {"CLAUDE.md", ".cursor/rules/handoff.mdc", "HANDOFF.md"} - {relative}
    assert not any((repo / o).exists() for o in others)  # only the target was written
    assert f"wrote {relative}" in out.replace("\n", " ") or relative in out


def test_acceptance_running_twice_does_not_duplicate(repo: Path) -> None:
    (repo / "CLAUDE.md").write_text("# Team rules\n\n- be nice\n")
    for _ in range(3):
        assert cb("handoff", "--from", "cursor", "--to", "claude")[0] == 0
    text = (repo / "CLAUDE.md").read_text()
    assert text.count(MARKER_START) == text.count("<context_handoff>") == 1
    assert text.startswith("# Team rules\n\n- be nice\n")
    for agent, path in (("cursor", ".cursor/rules/handoff.mdc"), ("generic", "HANDOFF.md")):
        cb("handoff", "--from", "claude", "--to", agent)
        cb("handoff", "--from", "claude", "--to", agent)
        assert (repo / path).read_text().count(MARKER_START) == 1


def test_acceptance_cleanup_leaves_user_content_in_claude_md(repo: Path) -> None:
    original = "# Team rules\n\n- be nice\n- keep functions small\n"
    (repo / "CLAUDE.md").write_text(original)
    cb("handoff", "--from", "cursor", "--to", "claude")
    cb("handoff", "--from", "cursor", "--to", "cursor")
    cb("handoff", "--from", "cursor", "--to", "generic")
    code, out = cb("clear", "--cleanup-adapters")
    assert code == 0, out
    assert (repo / "CLAUDE.md").read_text() == original
    assert not (repo / "HANDOFF.md").exists() and not (repo / ".cursor").exists()
    assert "No active session" not in out  # cleanup alone must not archive anything


def test_acceptance_last_handoff_is_updated(repo: Path) -> None:
    assert _state(repo)["last_handoff"] is None
    assert cb("handoff", "--from", "Claude-Code", "--to", "cursor")[0] == 0
    last = _state(repo)["last_handoff"]
    assert set(last) == {
        "timestamp",
        "from",
        "to",
        "raw_tokens",
        "compressed_tokens",
        "compression_ratio",
        "method",
    }
    assert (last["from"], last["to"], last["method"]) == ("claude", "cursor", "deterministic")
    expected = f"{(1 - last['compressed_tokens'] / last['raw_tokens']) * 100:.1f}%"
    assert last["compression_ratio"] == expected  # computed, never hard-coded
    assert last["timestamp"].endswith("Z")
    [row] = _rows(repo, "SELECT * FROM handoffs")
    assert (row["from_agent"], row["to_agent"], row["written_path"]) == (
        "claude",
        "cursor",
        ".cursor/rules/handoff.mdc",
    )
    assert row["raw_token_count"] == last["raw_tokens"]
    assert row["compressed_token_count"] == last["compressed_tokens"]
    assert row["session_id"] == _state(repo)["active_session_id"]
    assert row["payload_content"].startswith("# ContextBridge handoff")
    out = cb("status")[1]
    assert "Last handoff" in out and "claude → cursor" in out


# ----- handoff behaviour ---------------------------------------------------------------------


def test_dry_run_writes_nothing(repo: Path) -> None:
    code, out = cb("handoff", "--dry-run", "--from", "claude", "--to", "cursor")
    assert code == 0, out
    assert ".cursor/rules/handoff.mdc (not written)" in out.replace("\n", "")
    assert not (repo / ".cursor").exists()
    assert _state(repo)["last_handoff"] is None
    assert _rows(repo, "SELECT * FROM handoffs") == []


def test_unknown_agent_fails_before_any_work(repo: Path) -> None:
    code, out = cb("handoff", "--from", "claude", "--to", "gemini")
    assert code == 1
    assert "Unknown agent 'gemini'" in out and "claude, cursor, generic" in out
    assert _rows(repo, "SELECT * FROM handoffs") == []
    assert cb("handoff", "--dry-run", "--to", "gemini")[0] == 1


def test_from_agent_is_a_free_label(repo: Path) -> None:
    assert cb("handoff", "--from", "Gemini-CLI", "--to", "claude")[0] == 0
    assert _state(repo)["last_handoff"]["from"] == "gemini-cli"


def test_foreign_handoff_md_needs_force(repo: Path) -> None:
    (repo / "HANDOFF.md").write_text("# my own notes\n")
    code, out = cb("handoff", "--from", "claude", "--to", "generic")
    assert code == 1 and "not created by ContextBridge" in out and "--force" in out
    assert (repo / "HANDOFF.md").read_text() == "# my own notes\n"
    assert _rows(repo, "SELECT * FROM handoffs") == []  # a failed write is not recorded
    assert _state(repo)["last_handoff"] is None
    assert cb("handoff", "--from", "claude", "--to", "generic", "--force")[0] == 0
    assert MARKER_START in (repo / "HANDOFF.md").read_text()


def test_unbalanced_markers_in_claude_md_abort_cleanly(repo: Path) -> None:
    broken = f"mine\n{MARKER_START}\nhalf\n"
    (repo / "CLAUDE.md").write_text(broken)
    code, out = cb("handoff", "--from", "cursor", "--to", "claude")
    assert code == 1 and "Unbalanced" in out
    assert (repo / "CLAUDE.md").read_text() == broken


def test_print_flag_writes_and_prints_payload(repo: Path) -> None:
    result = runner.invoke(app, ["handoff", "--from", "a", "--to", "generic", "--print"])
    assert result.exit_code == 0
    assert result.stdout.startswith("# ContextBridge handoff")
    assert "wrote" in result.stderr and (repo / "HANDOFF.md").is_file()


def test_secrets_never_reach_the_files_or_database(repo: Path) -> None:
    cb("log", "key is AKIAIOSFODNN7EXAMPLE", "--tag", "blocker")
    write(repo, "config.py", 'TOKEN = "ghp_' + "Q1w2E3r4T5" * 4 + '"\n')
    write(repo, ".env", "API_SECRET=hunter2hunter2\n")
    for agent in ("claude", "cursor", "generic"):
        assert cb("handoff", "--from", "x", "--to", agent)[0] == 0
    blob = (
        b"".join(
            p.read_bytes()
            for p in (repo / "CLAUDE.md", repo / "HANDOFF.md", repo / ".cursor/rules/handoff.mdc")
        )
        + (repo / ".context" / "state.db").read_bytes()
    )
    for secret in (b"AKIAIOSFODNN7EXAMPLE", b"ghp_", b"hunter2"):
        assert secret not in blob


# ----- the loop guard: handoff output must not feed the next handoff -------------------------


def test_second_handoff_does_not_contain_the_first(repo: Path) -> None:
    assert cb("handoff", "--from", "a", "--to", "claude")[0] == 0
    assert cb("handoff", "--from", "a", "--to", "cursor")[0] == 0
    assert cb("handoff", "--from", "a", "--to", "generic")[0] == 0
    result = runner.invoke(app, ["handoff", "--dry-run", "--print", "--no-llm", "--to", "claude"])
    payload = result.stdout
    assert "context_handoff" not in payload and "contextbridge:" not in payload
    assert "CLAUDE.md" not in payload and "HANDOFF.md" not in payload
    assert "handoff.mdc" not in payload
    status = cb("status")[1]
    assert "CLAUDE.md" not in status and "HANDOFF.md" not in status


def test_real_claude_md_edits_are_still_reported(repo: Path) -> None:
    cb("handoff", "--from", "a", "--to", "claude")
    with (repo / "CLAUDE.md").open("a") as fh:
        fh.write("\n- new team rule\n")
    payload = runner.invoke(app, ["handoff", "--dry-run", "--print", "--no-llm"]).stdout
    assert "CLAUDE.md" in payload
    assert "contextbridge:" not in payload


def test_payload_size_is_stable_across_repeated_handoffs(repo: Path) -> None:
    sizes = []
    for _ in range(3):
        cb("handoff", "--from", "a", "--to", "claude")
        sizes.append(_state(repo)["last_handoff"]["raw_tokens"])
    assert max(sizes) - min(sizes) <= 5  # no snowballing


# ----- cb clear -------------------------------------------------------------------------------


def test_clear_session_archives_and_next_command_starts_fresh(repo: Path) -> None:
    old = _state(repo)["active_session_id"]
    code, out = cb("clear")  # no flags: --session
    assert code == 0 and "Archived session" in out
    assert _rows(repo, f"SELECT status FROM sessions WHERE id='{old}'")[0][0] == "archived"
    assert _state(repo)["active_session_id"] is None
    assert len(_rows(repo, "SELECT * FROM decision_logs")) == 2  # nothing deleted
    assert "No active session to archive" in cb("clear", "--session")[1]  # idempotent
    cb("log", "fresh start")
    assert _state(repo)["active_session_id"] not in (None, old)


def test_clear_all_asks_for_confirmation(repo: Path) -> None:
    cb("handoff", "--from", "a", "--to", "generic")
    code, out = cb("clear", "--all", input="n\n")
    assert code == 1 and "Cancelled" in out
    assert len(_rows(repo, "SELECT * FROM sessions")) == 1
    assert len(_rows(repo, "SELECT * FROM handoffs")) == 1
    flat = " ".join(out.split())  # Rich wraps long lines
    assert "permanently deletes" in flat and "2 decision logs" in flat and "1 handoffs" in flat


def test_clear_all_confirmed_deletes_everything(repo: Path) -> None:
    cb("handoff", "--from", "a", "--to", "generic")
    vector = repo / ".context" / "vector_db"
    vector.mkdir()
    (vector / "index.bin").write_bytes(b"x")
    code, out = cb("clear", "--all", input="y\n")
    assert code == 0, out
    for table in ("sessions", "decision_logs", "file_changes", "test_runs", "handoffs"):
        assert _rows(repo, f"SELECT * FROM {table}") == [], table
    state = _state(repo)
    assert state["active_session_id"] is None and state["last_handoff"] is None
    assert not vector.exists()
    assert (repo / ".context" / "config.json").exists()  # config survives
    assert (repo / "HANDOFF.md").exists()  # agent files survive without --cleanup-adapters
    assert cb("log", "after clear")[0] == 0  # the workspace still works


def test_clear_all_yes_skips_prompt(repo: Path) -> None:
    assert cb("clear", "--all", "--yes")[0] == 0
    assert _rows(repo, "SELECT * FROM sessions") == []


def test_clear_all_non_interactive_without_yes_does_not_delete(repo: Path) -> None:
    code, _ = cb("clear", "--all")  # no stdin to answer the prompt
    assert code != 0
    assert len(_rows(repo, "SELECT * FROM sessions")) == 1


def test_clear_session_and_all_conflict(repo: Path) -> None:
    code, out = cb("clear", "--session", "--all", "--yes")
    assert code == 1 and "not both" in out
    assert len(_rows(repo, "SELECT * FROM sessions")) == 1


def test_clear_combines_with_cleanup_adapters(repo: Path) -> None:
    cb("handoff", "--from", "a", "--to", "generic")
    code, out = cb("clear", "--session", "--cleanup-adapters")
    assert code == 0 and "Archived" in out and "HANDOFF.md" in out
    assert not (repo / "HANDOFF.md").exists()


def test_clear_cleanup_reports_nothing_to_do(repo: Path) -> None:
    assert "No injected agent files found" in cb("clear", "--cleanup-adapters")[1]


def test_clear_requires_init(git_repo: Path) -> None:
    assert cb("clear")[0] == 2
