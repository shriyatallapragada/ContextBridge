"""Copy migration, retained legacy reads, WAL safety and retry behavior."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from contextbridge.errors import StorageError
from contextbridge.service import ProjectService
from contextbridge.storage.workspace import Workspace, copy_legacy_workspace, find_context_dir


def legacy_workspace(root: Path) -> Path:
    service = ProjectService(root)
    service.init(install_hook=False)
    service.add_note("preserve me", "architecture")
    new = root / ".contextbridge"
    legacy = root / ".context"
    new.rename(legacy)
    (legacy / "extra.txt").write_text("keep this too")
    return legacy


def test_copy_retains_legacy_and_prefers_current(tmp_path: Path) -> None:
    legacy = legacy_workspace(tmp_path)
    original = {p.name: p.read_bytes() for p in legacy.iterdir() if p.is_file()}
    assert Workspace.locate(tmp_path).context_dir == legacy
    assert ProjectService(tmp_path).search("preserve")
    ProjectService(tmp_path).init(install_hook=False)
    current = tmp_path / ".contextbridge"
    assert current.is_dir()
    assert find_context_dir(tmp_path) == current
    assert (current / "extra.txt").read_text() == "keep this too"
    assert ProjectService(tmp_path).search("preserve")
    for name, data in original.items():
        if name.endswith(("-wal", "-shm", "cb.log")):
            continue
        assert (legacy / name).read_bytes() == data
    ProjectService(tmp_path).add_note("new only")
    with sqlite3.connect(legacy / "state.db") as db:
        assert (
            db.execute("SELECT count(*) FROM decision_logs WHERE content='new only'").fetchone()[0]
            == 0
        )
    assert Workspace(tmp_path).context_dir == current
    # Repeated init never overwrites the current database with the older copy.
    ProjectService(tmp_path).init(install_hook=False)
    assert ProjectService(tmp_path).search("new only")


def test_wal_data_is_included_and_source_unchanged(tmp_path: Path) -> None:
    legacy = legacy_workspace(tmp_path)
    connection = sqlite3.connect(legacy / "state.db")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "INSERT INTO decision_logs (id,session_id,content,tag) "
            "SELECT 'wal_note',id,'in WAL','note' FROM sessions LIMIT 1"
        )
        connection.commit()
        before = (legacy / "state.db").read_bytes()
        copy_legacy_workspace(tmp_path)
        assert (legacy / "state.db").read_bytes() == before
        assert ProjectService(tmp_path).search("in WAL")
    finally:
        connection.close()


def test_failed_copy_keeps_legacy_readable_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextbridge.storage.workspace as module

    legacy = legacy_workspace(tmp_path)
    original = module.shutil.copytree

    def fail(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(module.shutil, "copytree", fail)
    with pytest.raises(OSError):
        copy_legacy_workspace(tmp_path)
    assert not (tmp_path / ".contextbridge").exists()
    assert find_context_dir(tmp_path) == legacy
    assert not list(tmp_path.glob(".contextbridge-copy-*"))
    monkeypatch.setattr(module.shutil, "copytree", original)
    copy_legacy_workspace(tmp_path)
    assert ProjectService(tmp_path).search("preserve")


def test_conflicting_folder_preserved_with_legacy_fallback(tmp_path: Path) -> None:
    legacy = legacy_workspace(tmp_path)
    current = tmp_path / ".contextbridge"
    current.mkdir()
    (current / "user.txt").write_text("keep")
    with pytest.raises(StorageError):
        copy_legacy_workspace(tmp_path)
    assert (current / "user.txt").read_text() == "keep"
    assert find_context_dir(tmp_path) == legacy


def test_concurrent_copy_publishes_complete_workspace(tmp_path: Path) -> None:
    legacy_workspace(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: copy_legacy_workspace(tmp_path), range(2)))
    assert ProjectService(tmp_path).search("preserve")
    assert (tmp_path / ".context" / "extra.txt").is_file()


def test_old_state_config_and_subdirectory_discovery(tmp_path: Path) -> None:
    legacy = legacy_workspace(tmp_path)
    (legacy / "config.json").write_text(json.dumps({"llm": {"provider": "none"}}))
    sub = tmp_path / "src"
    sub.mkdir()
    assert ProjectService.for_path(sub).load_ai_settings().mode == "off"
    copy_legacy_workspace(tmp_path)
    assert ProjectService.for_path(sub).load_ai_settings().mode == "off"
