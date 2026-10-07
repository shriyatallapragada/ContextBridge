from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from contextbridge.errors import StorageError, UserError
from contextbridge.storage.db import (
    _MIGRATION_1,
    SCHEMA_VERSION,
    Database,
    _split_sql,
    new_id,
    parse_utc,
    utc_now,
)

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
    assert {
        "idx_decisions_session",
        "idx_changes_session",
        "idx_test_runs_session",
        "idx_changes_commit_file",
    } <= indexes


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


# ----- Phase 2: migration 2, file changes, test runs -----------------------------


def _v1_database(path: Path, *, with_test_runs: bool) -> None:
    conn = sqlite3.connect(path)
    for statement in _split_sql(_MIGRATION_1):
        conn.execute(statement)
    if with_test_runs:  # Phase 1 databases shipped test_runs inside migration 1.
        conn.execute(
            "CREATE TABLE test_runs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
            "command TEXT NOT NULL, exit_code INTEGER NOT NULL, passed INTEGER, failed INTEGER, "
            "skipped INTEGER, failures TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


@pytest.mark.parametrize("with_test_runs", [True, False])
def test_migration_1_to_2(tmp_path: Path, with_test_runs: bool) -> None:
    path = tmp_path / "state.db"
    _v1_database(path, with_test_runs=with_test_runs)
    assert "test_runs" not in _MIGRATION_1
    with Database.open(path, migrate=False) as db:
        assert db.migrate() == 1
        assert db.schema_version == 2 == SCHEMA_VERSION
        assert "test_runs" in _tables(db)
        s = db.create_session("main")
        assert db.add_test_run(s.id, "pytest", 0, passed=1).passed == 1


def test_file_change_roundtrip_and_commit_idempotency(db: Database) -> None:
    s = db.create_session("main")
    symbols = [{"name": "f", "kind": "function", "change": "modified", "signature": "def f()"}]
    first = db.add_file_change(
        s.id, "a.py", "modified", commit_sha="abc123", symbols=symbols, diff_snippet="@@ x"
    )
    assert first is not None
    assert db.has_commit("abc123")
    assert not db.has_commit("def456")
    # Same (commit, file) again is ignored rather than duplicated.
    assert db.add_file_change(s.id, "a.py", "modified", commit_sha="abc123") is None
    # Working-tree rows (no commit) are never deduplicated.
    assert db.add_file_change(s.id, "a.py", "modified") is not None
    assert db.add_file_change(s.id, "a.py", "modified") is not None
    changes = db.list_file_changes(s.id)
    assert len(changes) == 3
    stored = next(c for c in changes if c.commit_sha == "abc123")
    assert stored.symbols == symbols
    assert stored.diff_snippet == "@@ x"
    with pytest.raises(UserError):
        db.add_file_change(s.id, "a.py", "exploded")


def test_file_change_is_sanitized(db: Database) -> None:
    s = db.create_session("main")
    secret = "AKIAIOSFODNN7EXAMPLE"
    change = db.add_file_change(
        s.id,
        "cfg.py",
        "modified",
        commit_sha="abc",
        symbols=[{"name": "f", "kind": "function", "change": "added", "signature": secret}],
        diff_snippet=f"+key = '{secret}'",
    )
    assert change is not None
    row = db.conn.execute("SELECT symbols_modified, diff_snippet FROM file_changes").fetchone()
    assert secret not in row[0] and secret not in row[1]
    assert "[REDACTED:aws_access_key]" in row[1]


def test_test_run_roundtrip_sanitized_and_capped(db: Database) -> None:
    s = db.create_session("main")
    failures = [{"test": f"t{i}", "message": "token ghp_" + "a" * 36} for i in range(15)]
    run = db.add_test_run(
        s.id, "API_TOKEN=abc123 pytest", 1, passed=3, failed=15, skipped=0, failures=failures
    )
    assert len(run.failures) == 10
    last = db.last_test_run(s.id)
    assert last is not None and last.id == run.id
    assert (last.passed, last.failed, last.skipped, last.exit_code) == (3, 15, 0, 1)
    assert "abc123" not in last.command
    assert all("ghp_" not in f["message"] for f in last.failures)
    assert db.last_test_run(db.create_session("other").id) is None


def test_handoff_roundtrip_sanitized(db: Database) -> None:
    s = db.create_session("main")
    record = db.add_handoff(
        s.id,
        "claude",
        "cursor",
        raw_tokens=1000,
        compressed_tokens=250,
        method="deterministic",
        payload="payload AKIAIOSFODNN7EXAMPLE",
        written_path=".cursor/rules/handoff.mdc",
    )
    assert record.id.startswith("hand_")
    [stored] = db.list_handoffs(s.id)
    assert (stored.from_agent, stored.to_agent, stored.raw_token_count) == (
        "claude",
        "cursor",
        1000,
    )
    assert stored.compressed_token_count == 250 and stored.method == "deterministic"
    assert stored.written_path == ".cursor/rules/handoff.mdc"
    assert "AKIAIOSFODNN7EXAMPLE" not in stored.payload_content
    assert db.list_handoffs(db.create_session("other").id) == []


def test_delete_all_sessions_removes_handoffs_first(db: Database) -> None:
    s = db.create_session("main")
    db.add_decision(s.id, "d", "note")
    db.add_file_change(s.id, "a.py", "added", commit_sha="c1")
    db.add_test_run(s.id, "pytest", 0, passed=1)
    db.add_handoff(
        s.id,
        "a",
        "b",
        raw_tokens=1,
        compressed_tokens=1,
        method="m",
        payload="p",
        written_path=None,
    )
    assert db.count_all() == {
        "sessions": 1,
        "decision_logs": 1,
        "file_changes": 1,
        "test_runs": 1,
        "handoffs": 1,
    }
    deleted = db.delete_all_sessions()  # would violate the handoffs foreign key otherwise
    assert deleted["handoffs"] == 1 and deleted["sessions"] == 1
    assert set(db.count_all().values()) == {0}
