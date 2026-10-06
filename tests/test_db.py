from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from contextbridge.errors import StorageError, UserError
from contextbridge.storage.db import SCHEMA_VERSION, Database, new_id, parse_utc, utc_now

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database.open(tmp_path / "state.db")
    yield database  # type: ignore[misc]
    database.close()


def _tables(db: Database) -> set[str]:
    rows = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def test_schema_created_with_pragmas(db: Database) -> None:
    assert {"sessions", "decision_logs", "file_changes", "test_runs", "handoffs"} <= _tables(db)
    assert db.schema_version == SCHEMA_VERSION
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    indexes = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_decisions_session", "idx_changes_session"} <= indexes


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with Database.open(path, migrate=False) as first:
        assert first.migrate() == SCHEMA_VERSION
        assert first.migrate() == 0
    with Database.open(path) as second:
        assert second.schema_version == SCHEMA_VERSION


def test_newer_schema_rejected(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(StorageError):
        Database.open(path)


def test_ids_and_timestamps() -> None:
    assert re.fullmatch(r"sess_[0-9a-f]{12}", new_id("sess"))
    assert new_id("x") != new_id("x")
    now = utc_now()
    assert ISO_UTC.match(now)
    assert parse_utc(now).utcoffset() is not None
    assert parse_utc("2026-10-06 21:00:00").tzinfo is not None  # SQLite CURRENT_TIMESTAMP


def test_session_lifecycle(db: Database) -> None:
    s = db.create_session("feature/auth")
    assert s.id.startswith("sess_")
    assert s.status == "active"
    assert ISO_UTC.match(s.start_time)
    assert db.find_active_session("feature/auth") == s
    assert db.find_active_session("main") is None
    db.set_session_status(s.id, "archived")
    assert db.find_active_session("feature/auth") is None
    with pytest.raises(UserError):
        db.set_session_status(s.id, "bogus")


def test_add_decision_sanitizes(db: Database) -> None:
    s = db.create_session("main")
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    d = db.add_decision(s.id, f"Rotated token {secret}", "bug")
    assert secret not in d.content
    stored = db.conn.execute(
        "SELECT content, tag FROM decision_logs WHERE id=?", (d.id,)
    ).fetchone()
    assert secret not in stored["content"]
    assert "[REDACTED:github_token]" in stored["content"]
    assert stored["tag"] == "bug"


def test_add_decision_validation(db: Database) -> None:
    s = db.create_session("main")
    with pytest.raises(UserError):
        db.add_decision(s.id, "x", "nonsense")
    with pytest.raises(UserError):
        db.add_decision(s.id, "   ", "note")
    with pytest.raises(StorageError):
        db.add_decision("sess_doesnotexist", "x", "note")  # foreign key enforced


def test_list_and_count_decisions(db: Database) -> None:
    s = db.create_session("main")
    for i, tag in enumerate(["note", "bug", "bug", "blocker"]):
        db.add_decision(s.id, f"entry {i}", tag)
    recent = db.list_decisions(s.id, limit=2)
    assert [d.content for d in recent] == ["entry 3", "entry 2"]
    assert len(db.list_decisions(s.id)) == 4
    assert db.count_decisions_by_tag(s.id) == {"note": 1, "bug": 2, "blocker": 1}


def test_cascade_delete(db: Database) -> None:
    s = db.create_session("main")
    db.add_decision(s.id, "hello", "note")
    db.conn.execute("DELETE FROM sessions WHERE id=?", (s.id,))
    assert db.conn.execute("SELECT COUNT(*) FROM decision_logs").fetchone()[0] == 0


def test_check_constraint_on_tag(db: Database) -> None:
    s = db.create_session("main")
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO decision_logs (id, session_id, content, tag) VALUES ('d', ?, 'c', 'zzz')",
            (s.id,),
        )
