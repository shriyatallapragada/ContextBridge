from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from contextbridge.main import app

runner = CliRunner()


def cb(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def _decisions(repo: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(repo / ".context" / "state.db")
    try:
        return conn.execute("SELECT content, tag FROM decision_logs ORDER BY rowid").fetchall()
    finally:
        conn.close()


def test_init_creates_workspace(git_repo: Path) -> None:
    code, out = cb("init")
    assert code == 0, out
    assert "Database schema at v2" in out
    ctx = git_repo / ".context"
    assert (ctx / "state.db").is_file()
    assert (ctx / "config.json").is_file()
    state = json.loads((ctx / "state.json").read_text())
    assert state["schema_version"] == 1
    assert state["project_name"] == "project"
    assert state["active_branch"] == "main"
    assert state["active_session_id"].startswith("sess_")
    assert ".context/" in (git_repo / ".gitignore").read_text().splitlines()


def test_init_twice_is_safe(git_repo: Path) -> None:
    assert cb("init")[0] == 0
    state1 = json.loads((git_repo / ".context" / "state.json").read_text())
    assert cb("log", "keep me")[0] == 0

    code, out = cb("init")
    assert code == 0, out
    assert "Already initialized" in out
    assert "Database schema" not in out  # nothing to migrate the second time
    state2 = json.loads((git_repo / ".context" / "state.json").read_text())
    assert state2["active_session_id"] == state1["active_session_id"]
    assert (git_repo / ".gitignore").read_text().count(".context") == 1
    assert _decisions(git_repo) == [("keep me", "note")]


def test_context_dir_is_gitignored(git_repo: Path) -> None:
    (git_repo / ".gitignore").write_text("node_modules")  # no trailing newline
    assert cb("init")[0] == 0
    assert (git_repo / ".gitignore").read_text() == "node_modules\n.context/\n"
    check = subprocess.run(
        ["git", "-C", str(git_repo), "check-ignore", "-q", ".context/state.db"], check=False
    )
    assert check.returncode == 0
    status = subprocess.run(
        ["git", "-C", str(git_repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert ".context" not in status


def test_existing_gitignore_entry_respected(git_repo: Path) -> None:
    (git_repo / ".gitignore").write_text("/.context\n")
    assert cb("init")[0] == 0
    assert (git_repo / ".gitignore").read_text() == "/.context\n"


def test_init_from_subdirectory_uses_repo_root(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = git_repo / "src" / "pkg"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert cb("init")[0] == 0
    assert (git_repo / ".context" / "state.db").is_file()
    assert not (sub / ".context").exists()
    # Commands find .context/ by walking up.
    assert cb("log", "from subdir")[0] == 0
    assert _decisions(git_repo) == [("from subdir", "note")]


def test_log_stores_sanitized_row_with_tag(git_repo: Path) -> None:
    cb("init")
    secret = "AKIA" + "IOSFODNN7EXAMPLE"
    code, out = cb("log", f"x leaked {secret}", "--tag", "bug")
    assert code == 0, out
    assert secret not in out
    assert "Redacted 1 secret" in out
    rows = _decisions(git_repo)
    assert rows == [("x leaked [REDACTED:aws_access_key]", "bug")]
    log_text = (git_repo / ".context" / "cb.log").read_text()
    assert secret not in log_text


def test_log_acceptance_simple(git_repo: Path) -> None:
    cb("init")
    code, _ = cb("log", "x", "--tag", "bug")
    assert code == 0
    assert _decisions(git_repo) == [("x", "bug")]


def test_log_default_tag_and_invalid_tag(git_repo: Path) -> None:
    cb("init")
    assert cb("log", "plain")[0] == 0
    code, _ = cb("log", "bad", "--tag", "nonsense")
    assert code != 0
    assert _decisions(git_repo) == [("plain", "note")]


def test_log_empty_message_is_user_error(git_repo: Path) -> None:
    cb("init")
    code, out = cb("log", "   ")
    assert code == 1
    assert "empty" in out


def test_commands_require_init(git_repo: Path) -> None:
    for args in (("log", "x"), ("status",)):
        code, out = cb(*args)
        assert code == 2
        assert "cb init" in out
        assert "NotInitializedError" not in out  # no stray logging to the terminal


def test_repo_option(git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert cb("--repo", str(git_repo), "init")[0] == 0
    assert cb("--repo", str(git_repo), "log", "via --repo", "-t", "todo")[0] == 0
    assert _decisions(git_repo) == [("via --repo", "todo")]


def test_init_outside_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    code, out = cb("init")
    assert code == 0, out
    state = json.loads((plain / ".context" / "state.json").read_text())
    assert state["active_branch"] == "(no-branch)"


def test_status_shows_session_and_decisions(git_repo: Path) -> None:
    cb("init")
    cb("log", "Use WAL mode", "--tag", "architecture")
    cb("log", "CI is red", "--tag", "blocker")
    code, out = cb("status")
    assert code == 0, out
    assert "main" in out
    assert "sess_" in out
    assert "Use WAL mode" in out
    assert "CI is red" in out
    assert "architecture 1" in out
    assert "blocker 1" in out


def test_status_empty_session(git_repo: Path) -> None:
    cb("init")
    code, out = cb("status")
    assert code == 0
    assert "No decisions logged yet" in out


def test_invalid_config_is_env_error(git_repo: Path) -> None:
    cb("init")
    (git_repo / ".context" / "config.json").write_text('{"llm": {"provider": "nope"}}')
    code, out = cb("init")
    assert code == 2
    assert "llm.provider" in out


def test_version_and_help() -> None:
    code, out = cb("--version")
    assert code == 0 and "cb " in out
    code, out = cb("--help")
    assert code == 0
    for name in ("init", "log", "status"):
        assert name in out
