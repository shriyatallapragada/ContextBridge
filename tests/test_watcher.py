"""Metadata batching through the engine and explicit CLI entry point."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from contextbridge.core.watcher import ProjectWatcher
from contextbridge.main import app
from contextbridge.service import ProjectService
from contextbridge.storage.workspace import Workspace
from tests.helpers import commit_all, write


def watcher(root: Path, **kwargs) -> ProjectWatcher:
    service = ProjectService(root)
    service.init(install_hook=False)
    return ProjectWatcher(service.ws, **kwargs)


def batches(root: Path) -> list[dict]:
    with Workspace(root).open_db() as db:
        return [dict(row) for row in db.conn.execute("SELECT * FROM change_batches")]


def test_debounce_repeated_saves_and_maximum_window(tmp_path: Path) -> None:
    watch = watcher(tmp_path, debounce=2, batch_window=30, agent="codex", notes="formatting")
    watch.poll(0)
    write(tmp_path, "code.py", "first")
    watch.poll(1)
    write(tmp_path, "code.py", "second")
    assert watch.poll(2) is None
    assert watch.poll(3) is None
    assert watch.poll(4) == "1 files changed in 3.0s"
    [batch] = batches(tmp_path)
    assert batch["agent"] == "codex" and batch["notes"] == "formatting"
    assert batch["event_count"] == 2
    assert json.loads(batch["files"]) == [
        {
            "path": "code.py",
            "change_type": "added",
            "summary": "added; 0 -> 6 bytes (+6)",
        }
    ]
    for second in range(5, 36):
        write(tmp_path, "code.py", str(second))
        watch.poll(second)
    assert len(batches(tmp_path)) == 2
    assert batches(tmp_path)[1]["duration_seconds"] == 30


def test_ignores_own_writes_secrets_dependencies_and_nested_gitignore(tmp_path: Path) -> None:
    write(tmp_path, ".gitignore", "ignored/\n*.scratch\n")
    write(tmp_path, "src/.gitignore", "*.private\n!keep.private\n")
    watch = watcher(tmp_path)
    watch.poll(0)
    for path in [
        ".context/x",
        ".contextbridge/cb.log",
        "AGENTS.md",
        "HANDOFF.md",
        "cb.log",
        "src/AGENTS.md",
        "node_modules/x.py",
        ".venv/lib/x.py",
        "build/x",
        "dist/x",
        ".env",
        ".env.local",
        "secret.key",
        "id_rsa",
        "ignored/x",
        "src/a.private",
        "x.scratch",
    ]:
        write(tmp_path, path, "private content")
    write(tmp_path, "src/keep.private", "eligible")
    watch.poll(1)
    watch.flush(2)
    assert watch.metrics.events == 1
    assert [item["path"] for item in json.loads(batches(tmp_path)[0]["files"])] == [
        "src/keep.private"
    ]
    watch.poll(4)
    watch.flush(6)
    assert len(batches(tmp_path)) == 1  # storage writes never feed back into the watcher


def test_no_file_content_read_or_stored_and_restart_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch = watcher(tmp_path)
    write(tmp_path, "code.py", "unique-source-secret")
    watch.poll(0)
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.name != "code.py", "watcher must only stat source files"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    with open(tmp_path / "code.py", "a") as stream:
        stream.write("more-source")
    watch.poll(1)
    watch.flush(2)
    with Workspace(tmp_path).open_db() as db:
        dump = "\n".join(db.conn.iterdump())
    assert "unique-source-secret" not in dump and "more-source" not in dump
    assert "code.py" in dump
    restarted = ProjectWatcher(Workspace(tmp_path))
    restarted.poll(5)
    restarted.poll(9)
    assert restarted.metrics.events == 0
    assert len(batches(tmp_path)) == 1


def test_add_delete_rename_symlinks_and_temporary_files(tmp_path: Path) -> None:
    write(tmp_path, "old.py", "1234")
    watch = watcher(tmp_path)
    watch.poll(0)
    (tmp_path / "old.py").rename(tmp_path / "new.py")
    (tmp_path / "link.py").symlink_to("/etc/passwd")
    write(tmp_path, "temporary.py", "temporary")
    watch.poll(1)
    (tmp_path / "temporary.py").unlink()
    watch.poll(2)
    watch.flush(4)
    files = json.loads(batches(tmp_path)[0]["files"])
    assert [(item["path"], item["change_type"]) for item in files] == [
        ("new.py", "added"),
        ("old.py", "deleted"),
    ]


def test_failure_retries_entire_pending_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextbridge.storage.db import Database

    watch = watcher(tmp_path)
    watch.poll(0)
    write(tmp_path, "file.py", "x")
    watch.poll(1)
    original = Database.add_change_batch

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Database, "add_change_batch", fail)
    with pytest.raises(OSError):
        watch.flush(2)
    assert watch.pending and not batches(tmp_path)
    monkeypatch.setattr(Database, "add_change_batch", original)
    watch.flush(3)
    assert len(batches(tmp_path)) == 1
    assert not watch.pending


def test_storage_batch_rolls_back_on_invalid_file(tmp_path: Path) -> None:
    watch = watcher(tmp_path)
    with watch.ws.open_db() as db:
        session, _ = watch.ws.active_session(db)
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            db.add_change_batch(
                session.id,
                [
                    {"path": "ok.py", "change_type": "added", "summary": "+1 byte"},
                    {"path": "bad.py", "change_type": "invalid", "summary": "+1 byte"},
                ],
                1,
                2,
            )
        assert db.conn.execute("SELECT count(*) FROM change_batches").fetchone()[0] == 0
        assert db.list_file_changes(session.id) == []


def test_new_ignore_rule_does_not_log_excluded_file_as_deleted(tmp_path: Path) -> None:
    write(tmp_path, "file.private", "private")
    watch = watcher(tmp_path)
    watch.poll(0)
    write(tmp_path, ".gitignore", "*.private\n")
    watch.poll(1)
    watch.flush(3)
    assert [item["path"] for item in json.loads(batches(tmp_path)[0]["files"])] == [".gitignore"]


def test_commit_metadata_never_contains_source_or_own_outputs(git_repo: Path) -> None:
    service = ProjectService(git_repo)
    service.init(install_hook=False)
    write(git_repo, "code.py", 'def private_symbol():\n    return "unredactable-source-text"\n')
    write(git_repo, "AGENTS.md", "own output")
    commit_all(git_repo, "update")
    service.ingest_head()
    with service.ws.open_db() as db:
        session, _ = service.ws.active_session(db)
        rows = db.list_file_changes(session.id)
        assert "AGENTS.md" not in [row.file_path for row in rows]
        assert all(row.symbols == [] for row in rows)
        assert "unredactable-source-text" not in "\n".join(db.conn.iterdump())


def test_cli_watch_is_explicit_and_stops_cleanly(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["--repo", str(tmp_path), "init", "--no-hooks"]).exit_code == 0
    assert not batches(tmp_path)
    result = runner.invoke(
        app, ["--repo", str(tmp_path), "watch", "--seconds", "0.1", "--interval", "0.05"]
    )
    assert result.exit_code == 0, result.output
    assert "Watching" in result.stdout and "0 batches" in result.stdout
    assert not batches(tmp_path)


def test_stop_flushes_pending_changes(tmp_path: Path) -> None:
    watch = watcher(tmp_path)
    watch.poll()
    write(tmp_path, "last.py", "last save")
    stop = threading.Event()
    stop.set()
    watch.run(stop)
    assert len(batches(tmp_path)) == 1


def test_directory_removed_during_scan_does_not_stop_watching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    write(tmp_path, "removed/file.py", "x")
    watch = watcher(tmp_path)
    watch.poll(0)
    original = os.scandir

    def disappear(path):
        if Path(path).name == "removed":
            (Path(path) / "file.py").unlink()
            Path(path).rmdir()
            raise FileNotFoundError("checkout removed directory")
        return original(path)

    monkeypatch.setattr(os, "scandir", disappear)
    watch.poll(1)
    watch.flush(3)
    [file] = json.loads(batches(tmp_path)[0]["files"])
    assert file["path"] == "removed/file.py" and file["change_type"] == "deleted"


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX Ctrl-C signal smoke check")
def test_foreground_cli_ctrl_c_flushes_and_exits(tmp_path: Path) -> None:
    import os
    import signal
    import subprocess
    import sys
    import time

    ProjectService(tmp_path).init(install_hook=False)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "contextbridge.main",
            "--repo",
            str(tmp_path),
            "watch",
            "--interval",
            "0.05",
            "--debounce",
            "10",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert process.stdout is not None
        assert "Watching" in process.stdout.readline()
        time.sleep(0.2)
        write(tmp_path, "saved.py", "source must not be logged")
        time.sleep(0.15)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert "1 batches" in stdout
        [batch] = batches(tmp_path)
        assert json.loads(batch["files"])[0]["path"] == "saved.py"
        assert "source must not be logged" not in str(batch)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
