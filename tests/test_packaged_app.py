"""The packaged app is not a Python interpreter: the git hook must call it in CLI mode.

If the hook ran `<app> -m contextbridge.main ...`, every commit would open the window.
"""

from __future__ import annotations

import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from contextbridge.core.git_observer import GitObserver, hook_block, hook_invocation
from contextbridge.gui import app as gui_app
from tests.helpers import commit_all, git, write


def test_hook_words_for_source_and_packaged() -> None:
    exe = shlex.quote(sys.executable)
    assert hook_invocation(frozen=False) == f"{exe} -m contextbridge.main"
    assert hook_invocation(frozen=True) == f"{exe} --cli"
    assert "--cli hook post-commit" in hook_block(frozen=True)
    assert "-m contextbridge.main" not in hook_block(frozen=True)
    assert "-m contextbridge.main hook post-commit" in hook_block(frozen=False)
    assert "--cli" not in hook_block(frozen=False)


def test_frozen_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextbridge.core.git_observer import is_frozen

    assert not is_frozen()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert is_frozen()
    assert "--cli" in hook_block()


def test_real_commit_runs_the_app_in_cli_mode(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A packaged-style hook calls the 'app' with `--cli hook post-commit` and nothing else."""
    log = tmp_path / "app-calls.log"
    fake_app = tmp_path / "ContextBridge"
    fake_app.write_text(f'#!/bin/sh\necho "$@" >> {shlex.quote(str(log))}\n')
    fake_app.chmod(fake_app.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(sys, "executable", str(fake_app))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    GitObserver.open(git_repo).install_hook()
    write(git_repo, "a.txt", "x")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-q", "-m", "trigger the hook")

    deadline = time.time() + 15
    while time.time() < deadline and not log.exists():
        time.sleep(0.1)
    assert log.exists(), "the hook never called the app"
    assert log.read_text().split() == ["--cli", "hook", "post-commit"]


def test_cli_mode_behaves_like_cb(git_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as done:
        gui_app.main(["--cli", "--version"])
    assert done.value.code == 0
    assert capsys.readouterr().out.startswith("cb ")


def test_cli_mode_records_a_commit_like_the_hook_does(git_repo: Path) -> None:
    """What the packaged app does when git calls it: record the latest commit."""
    import sqlite3

    write(git_repo, "svc.py", "def a():\n    return 1\n")
    commit_all(git_repo, "add svc")
    with pytest.raises(SystemExit):
        gui_app.main(["--cli", "init", "--no-hooks"])
    commit_all(git_repo, "ignore .context/")
    write(git_repo, "svc.py", "def a():\n    return 2\n")
    sha = commit_all(git_repo, "change a")
    with pytest.raises(SystemExit) as done:
        gui_app.main(["--cli", "hook", "post-commit"])
    assert done.value.code == 0
    conn = sqlite3.connect(git_repo / ".context" / "state.db")
    try:
        rows = conn.execute(
            "SELECT file_path, symbols_modified FROM file_changes WHERE commit_sha = ?", (sha,)
        ).fetchall()
    finally:
        conn.close()
    assert rows and rows[0][0] == "svc.py" and '"a"' in rows[0][1]


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert gui_app.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("ContextBridge ")


def test_cli_mode_survives_missing_console_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windowed apps can start without stdout/stderr; the hook must not crash on that."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    with pytest.raises(SystemExit) as done:
        gui_app.main(["--cli", "--version"])
    assert done.value.code == 0


def test_self_test_passes_in_source_mode(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("PySide6.QtWidgets")
    from contextbridge.synthesis import token_counter

    # It checks for the real tokenizer, so start from a clean tokenizer state.
    monkeypatch.delenv("CB_TOKENIZER", raising=False)
    token_counter._encoding.cache_clear()
    try:
        assert gui_app.main(["--self-test"]) == 0
        assert capsys.readouterr().out.strip().endswith("OK")
    finally:
        token_counter._encoding.cache_clear()


def test_cb_gui_without_pyside_gives_a_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from contextbridge.main import app

    monkeypatch.setitem(sys.modules, "contextbridge.gui.app", None)  # import -> ImportError
    result = CliRunner().invoke(app, ["gui"])
    assert result.exit_code == 2
    assert "extra package" in result.output and "contextbridge[gui]" in result.output


def test_path_extension_adds_common_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/nonexistent-only")
    gui_app._extend_path()
    assert "/usr/bin" in __import__("os").environ["PATH"].split(":")


def test_subprocess_cli_mode_is_not_a_window() -> None:
    """`--cli` returns immediately and prints CLI output; it never starts Qt."""
    proc = subprocess.run(
        [sys.executable, "-m", "contextbridge.gui.app", "--cli", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0 and proc.stdout.startswith("cb ")
