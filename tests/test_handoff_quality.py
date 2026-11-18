"""Quality and normal CLI entry points for shared project/chat handoffs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from contextbridge import clipboard
from contextbridge.chat_bridge import condense_chat
from contextbridge.main import app
from contextbridge.service import ProjectService
from contextbridge.storage.db import parse_utc
from contextbridge.synthesis.document import TITLES
from contextbridge.synthesis.token_counter import count_tokens
from tests.helpers import write

runner = CliRunner()


def service(root: Path) -> ProjectService:
    write(
        root,
        "pyproject.toml",
        '[project]\nname="student-app"\nversion="0.1.0"\n'
        'description="A local study planner for students."\n',
    )
    svc = ProjectService(root)
    svc.init(install_hook=False)
    svc.save_ai_settings("off")
    return svc


@pytest.mark.parametrize("budget", [200, 400, 1000, 2000])
def test_reserved_sections_rank_source_above_many_tests(tmp_path: Path, budget: int) -> None:
    svc = service(tmp_path)
    svc.add_note("Done: completed offline capture")
    svc.add_note("Keep SQLite", "architecture")
    svc.add_note("Build chat import", "todo")
    with svc.ws.open_db() as db:
        session, _ = svc.ws.active_session(db)
        db.add_file_change(session.id, "engine.py", "modified", diff_snippet="+5 / -2 lines")
        for number in range(100):
            db.add_file_change(
                session.id,
                f"tests/test_{number}.py",
                "modified",
                symbols=[{"name": f"test_function_{number}", "change": "added"}],
            )
        db.add_test_run(session.id, "pytest", 0, passed=511, failed=0, skipped=0)
    result = svc.prepare_handoff(to_agent="codex", budget=budget, use_llm=False)
    text = result.handoff.payload
    assert count_tokens(text) <= budget
    assert all("## " + title + "\n- " in text for title in TITLES)
    assert "(trimmed)" not in text and "test_function_" not in text
    assert "tests/test_" not in text and result.active_files == ["engine.py"]
    if budget >= 400:
        assert "local study planner" in text
        assert "completed offline capture" in text
        assert "Keep SQLite" in text and "Build chat import" in text
        assert "100 test files changed, 511 passing" in text
        assert "engine.py" in text


def test_logged_goal_overrides_metadata(tmp_path: Path) -> None:
    svc = service(tmp_path)
    svc.add_note("Goal: Help students hand context between coding tools")
    assert "Help students hand context" in svc.prepare_handoff(use_llm=False).handoff.payload


def test_test_run_age_and_staleness_use_actual_source_edit_time(git_repo: Path) -> None:
    from tests.helpers import commit_all

    svc = service(git_repo)
    write(git_repo, "engine.py", "value = 1\n")
    commit_all(git_repo, "source")
    svc.ingest_head()
    with svc.ws.open_db() as db:
        session, _ = svc.ws.active_session(db)
        run = db.add_test_run(session.id, "pytest", 0, passed=7, failed=0, skipped=0)
    tested = parse_utc(run.timestamp).timestamp()
    os.utime(git_repo / "engine.py", (tested - 5, tested - 5))
    fresh = svc.prepare_handoff(use_llm=False).handoff.payload
    assert "Last run" in fresh and "ago" in fresh and "STALE" not in fresh
    write(git_repo, "engine.py", "value = 2\n")
    os.utime(git_repo / "engine.py", (tested + 5, tested + 5))
    assert "STALE" in svc.prepare_handoff(use_llm=False).handoff.payload


def test_to_without_from_budget_and_adapter_layout(tmp_path: Path) -> None:
    svc = service(tmp_path)
    result = runner.invoke(
        app, ["--repo", str(tmp_path), "handoff", "--to", "codex", "--budget", "400", "--no-llm"]
    )
    assert result.exit_code == 0, result.output
    text = (tmp_path / "AGENTS.md").read_text()
    assert "## Goal" in text and "## Files to read first" in text
    assert "## Active files" not in text
    assert svc.ws.load_config().handoff.target_tokens == 2000  # flag does not change config


def test_clipboard_only_and_dry_run_do_not_write_agent_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service(tmp_path)
    copied = []
    monkeypatch.setattr(clipboard, "copy_text", copied.append)
    result = runner.invoke(app, ["--repo", str(tmp_path), "handoff", "--copy", "--no-llm"])
    assert result.exit_code == 0, result.output
    assert copied and "## Goal" in copied[0]
    result = runner.invoke(
        app, ["--repo", str(tmp_path), "handoff", "--to", "codex", "--dry-run", "--no-llm"]
    )
    assert result.exit_code == 0 and not (tmp_path / "AGENTS.md").exists()


@pytest.mark.parametrize("long", [False, True])
def test_chat_condenses_shared_sections_with_secrets_redacted(long: bool) -> None:
    transcript = ("User: old details\n" * 2000 if long else "") + (
        "User: Goal: Build a planner\nAssistant: Decision: Use SQLite\n"
        "Assistant: Done: Added notes\nUser: Blocker: Login fails\n"
        "User: Next: Fix login\nUser: key AKIAIOSFODNN7EXAMPLE\n"
    )
    text = condense_chat(transcript, budget=400)
    assert count_tokens(text) <= 400
    assert all("## " + title in text for title in (*TITLES[:-1], "Files"))
    assert "Use SQLite" in text and "Fix login" in text and "Login fails" in text
    assert "AKIAIOSFODNN7EXAMPLE" not in text and "(trimmed)" not in text


def test_import_chat_stdin_file_and_clipboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = "Goal: Build a planner\nDecision: SQLite\nNext: Add notes\n"
    file = tmp_path / "chat.txt"
    file.write_text(chat)
    monkeypatch.setattr(clipboard, "read_text", lambda: chat)
    copied = []
    monkeypatch.setattr(clipboard, "copy_text", copied.append)
    for args in ([], ["--file", str(file)], ["--clipboard", "--copy"]):
        result = runner.invoke(
            app, ["--repo", str(tmp_path), "import-chat", *args, "--budget", "300"], input=chat
        )
        assert result.exit_code == 0, result.output
        assert "## Goal" in result.stdout and "SQLite" in result.stdout
        assert count_tokens(result.stdout) <= 300
    assert copied
    assert not (tmp_path / ".contextbridge").exists()


def test_import_chat_reports_empty_input():
    result = runner.invoke(app, ["import-chat"], input="")
    assert result.exit_code == 1 and "Paste" in result.output
