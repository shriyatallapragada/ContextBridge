"""SQLite storage: schema, versioned migrations, and queries.

The schema version lives in ``PRAGMA user_version``. Migrations are applied in order
inside a transaction. All timestamps are UTC ISO-8601 strings, and all free text is
sanitized here as a last line of defence even if callers already did so.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from contextbridge.core.sanitizer import sanitize
from contextbridge.errors import StorageError, UserError

SESSION_STATUSES: Final = ("active", "archived", "handed_off")
DECISION_TAGS: Final = ("note", "bug", "architecture", "blocker", "todo")
CHANGE_TYPES: Final = ("modified", "added", "deleted", "renamed")
MAX_STORED_FAILURES: Final = 10

_MIGRATION_1: Final = """
CREATE TABLE IF NOT EXISTS sessions (
  id           TEXT PRIMARY KEY,
  branch_name  TEXT NOT NULL,
  start_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  status       TEXT CHECK(status IN ('active','archived','handed_off')) DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS decision_logs (
  id         TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  content    TEXT NOT NULL,
  tag        TEXT CHECK(tag IN ('note','bug','architecture','blocker','todo')) DEFAULT 'note',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS file_changes (
  id               TEXT PRIMARY KEY,
  session_id       TEXT NOT NULL,
  commit_sha       TEXT,
  file_path        TEXT NOT NULL,
  change_type      TEXT CHECK(change_type IN ('modified','added','deleted','renamed')),
  symbols_modified TEXT,
  diff_snippet     TEXT,
  timestamp        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS handoffs (
  id                     TEXT PRIMARY KEY,
  session_id             TEXT,
  from_agent             TEXT NOT NULL,
  to_agent               TEXT NOT NULL,
  raw_token_count        INTEGER,
  compressed_token_count INTEGER,
  method                 TEXT,
  payload_content        TEXT NOT NULL,
  written_path           TEXT,
  created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_session ON decision_logs(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_changes_session   ON file_changes(session_id, timestamp);
"""

# v2 (Phase 2): test results from `cb run`, and commit-aware file changes. Uses IF NOT
# EXISTS because early v1 databases already created `test_runs`.
_MIGRATION_2: Final = """
CREATE TABLE IF NOT EXISTS test_runs (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  command     TEXT NOT NULL,
  exit_code   INTEGER NOT NULL,
  passed      INTEGER, failed INTEGER, skipped INTEGER,
  failures    TEXT,
  timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_test_runs_session ON test_runs(session_id, timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS idx_changes_commit_file
  ON file_changes(commit_sha, file_path) WHERE commit_sha IS NOT NULL;
"""

# Index i holds the script that upgrades the schema from version i to i + 1.
MIGRATIONS: Final[tuple[str, ...]] = (_MIGRATION_1, _MIGRATION_2)
SCHEMA_VERSION: Final = len(MIGRATIONS)


def new_id(prefix: str) -> str:
    """Return an ID like ``sess_a1b2c3d4e5f6``."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    """Current time as a UTC ISO-8601 string (``2026-10-06T21:00:00Z``)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    """Parse a stored timestamp (ISO-8601 or SQLite ``CURRENT_TIMESTAMP``) as aware UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Session:
    id: str
    branch_name: str
    start_time: str
    last_updated: str
    status: str


@dataclass(frozen=True)
class Decision:
    id: str
    session_id: str
    content: str
    tag: str
    created_at: str


@dataclass(frozen=True)
class FileChange:
    id: str
    session_id: str
    commit_sha: str | None
    file_path: str
    change_type: str
    symbols: list[dict[str, Any]]
    diff_snippet: str | None
    timestamp: str


@dataclass(frozen=True)
class TestRun:
    __test__ = False  # Not a pytest test class.

    id: str
    session_id: str
    command: str
    exit_code: int
    passed: int | None
    failed: int | None
    skipped: int | None
    failures: list[dict[str, str]]
    timestamp: str


def _load_json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        branch_name=row["branch_name"],
        start_time=row["start_time"],
        last_updated=row["last_updated"],
        status=row["status"],
    )


def _row_to_decision(row: sqlite3.Row) -> Decision:
    return Decision(
        id=row["id"],
        session_id=row["session_id"],
        content=row["content"],
        tag=row["tag"],
        created_at=row["created_at"],
    )


def _row_to_file_change(row: sqlite3.Row) -> FileChange:
    return FileChange(
        id=row["id"],
        session_id=row["session_id"],
        commit_sha=row["commit_sha"],
        file_path=row["file_path"],
        change_type=row["change_type"],
        symbols=_load_json_list(row["symbols_modified"]),
        diff_snippet=row["diff_snippet"],
        timestamp=row["timestamp"],
    )


def _row_to_test_run(row: sqlite3.Row) -> TestRun:
    return TestRun(
        id=row["id"],
        session_id=row["session_id"],
        command=row["command"],
        exit_code=int(row["exit_code"]),
        passed=row["passed"],
        failed=row["failed"],
        skipped=row["skipped"],
        failures=_load_json_list(row["failures"]),
        timestamp=row["timestamp"],
    )


def _sanitize_json(value: Any) -> Any:
    """Sanitize every string inside a JSON-like structure."""
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, list):
        return [_sanitize_json(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize_json(v) for k, v in value.items()}
    return value


class Database:
    """Thin synchronous wrapper around the ``state.db`` connection."""

    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        self.conn = conn
        self.path = path

    # ----- lifecycle -------------------------------------------------------

    @classmethod
    def open(cls, path: Path, *, migrate: bool = True) -> Database:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot open database {path}: {exc}") from exc
        db = cls(conn, path)
        if migrate:
            db.migrate()
        return db

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # ----- migrations ------------------------------------------------------

    @property
    def schema_version(self) -> int:
        row = self.conn.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def migrate(self) -> int:
        """Apply pending migrations; return the number applied. Safe to call repeatedly."""
        current = self.schema_version
        if current > SCHEMA_VERSION:
            raise StorageError(
                f"Database schema v{current} is newer than this ContextBridge (v{SCHEMA_VERSION}).",
                hint="Upgrade ContextBridge.",
            )
        applied = 0
        for version in range(current, SCHEMA_VERSION):
            try:
                with self.transaction() as conn:
                    for statement in _split_sql(MIGRATIONS[version]):
                        conn.execute(statement)
                    # PRAGMA does not accept bound parameters; value is an int we control.
                    conn.execute(f"PRAGMA user_version = {version + 1}")
            except sqlite3.Error as exc:
                raise StorageError(f"Migration to v{version + 1} failed: {exc}") from exc
            applied += 1
        return applied

    # ----- sessions --------------------------------------------------------

    def create_session(self, branch_name: str) -> Session:
        now = utc_now()
        sid = new_id("sess")
        self.conn.execute(
            "INSERT INTO sessions (id, branch_name, start_time, last_updated, status) "
            "VALUES (?, ?, ?, ?, 'active')",
            (sid, sanitize(branch_name), now, now),
        )
        session = self.get_session(sid)
        assert session is not None
        return session

    def get_session(self, session_id: str) -> Session | None:
        row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return _row_to_session(row) if row else None

    def find_active_session(self, branch_name: str) -> Session | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE branch_name = ? AND status = 'active' "
            "ORDER BY last_updated DESC LIMIT 1",
            (branch_name,),
        ).fetchone()
        return _row_to_session(row) if row else None

    def touch_session(self, session_id: str) -> None:
        self.conn.execute(
            "UPDATE sessions SET last_updated = ? WHERE id = ?", (utc_now(), session_id)
        )

    def set_session_status(self, session_id: str, status: str) -> None:
        if status not in SESSION_STATUSES:
            raise UserError(f"Invalid session status {status!r}.")
        self.conn.execute(
            "UPDATE sessions SET status = ?, last_updated = ? WHERE id = ?",
            (status, utc_now(), session_id),
        )

    # ----- decisions -------------------------------------------------------

    def add_decision(self, session_id: str, content: str, tag: str = "note") -> Decision:
        if tag not in DECISION_TAGS:
            raise UserError(f"Invalid tag {tag!r}; expected one of: {', '.join(DECISION_TAGS)}.")
        clean = sanitize(content).strip()
        if not clean:
            raise UserError("Log message is empty.")
        did = new_id("dec")
        now = utc_now()
        try:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT INTO decision_logs (id, session_id, content, tag, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (did, session_id, clean, tag, now),
                )
                conn.execute("UPDATE sessions SET last_updated = ? WHERE id = ?", (now, session_id))
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"Cannot store decision: {exc}") from exc
        return Decision(id=did, session_id=session_id, content=clean, tag=tag, created_at=now)

    def list_decisions(self, session_id: str, *, limit: int | None = None) -> list[Decision]:
        sql = (
            "SELECT * FROM decision_logs WHERE session_id = ? ORDER BY created_at DESC, rowid DESC"
        )
        params: tuple[object, ...] = (session_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (session_id, limit)
        return [_row_to_decision(r) for r in self.conn.execute(sql, params).fetchall()]

    def count_decisions_by_tag(self, session_id: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT tag, COUNT(*) AS n FROM decision_logs WHERE session_id = ? GROUP BY tag",
            (session_id,),
        ).fetchall()
        return {r["tag"]: int(r["n"]) for r in rows}

    # ----- file changes ----------------------------------------------------

    def has_commit(self, commit_sha: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM file_changes WHERE commit_sha = ? LIMIT 1", (commit_sha,)
        ).fetchone()
        return row is not None

    def last_commit_sha(self, session_id: str) -> str | None:
        """The most recently recorded commit of a session."""
        row = self.conn.execute(
            "SELECT commit_sha FROM file_changes WHERE session_id = ? AND commit_sha IS NOT NULL "
            "ORDER BY timestamp DESC, rowid DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return str(row[0]) if row else None

    def add_file_change(
        self,
        session_id: str,
        file_path: str,
        change_type: str,
        *,
        commit_sha: str | None = None,
        symbols: list[dict[str, Any]] | None = None,
        diff_snippet: str | None = None,
    ) -> FileChange | None:
        """Store one file change. Returns None if this (commit, file) is already recorded."""
        if change_type not in CHANGE_TYPES:
            raise UserError(f"Invalid change type {change_type!r}.")
        fid = new_id("chg")
        now = utc_now()
        clean_path = sanitize(file_path)
        clean_symbols = _sanitize_json(symbols or [])
        clean_snippet = sanitize(diff_snippet) if diff_snippet is not None else None
        try:
            with self.transaction() as conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO file_changes (id, session_id, commit_sha, file_path, "
                    "change_type, symbols_modified, diff_snippet, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        fid,
                        session_id,
                        commit_sha,
                        clean_path,
                        change_type,
                        json.dumps(clean_symbols),
                        clean_snippet,
                        now,
                    ),
                )
                if cur.rowcount == 0:
                    return None
                conn.execute("UPDATE sessions SET last_updated = ? WHERE id = ?", (now, session_id))
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"Cannot store file change: {exc}") from exc
        return FileChange(
            id=fid,
            session_id=session_id,
            commit_sha=commit_sha,
            file_path=clean_path,
            change_type=change_type,
            symbols=clean_symbols,
            diff_snippet=clean_snippet,
            timestamp=now,
        )

    def list_file_changes(self, session_id: str, *, limit: int | None = None) -> list[FileChange]:
        sql = "SELECT * FROM file_changes WHERE session_id = ? ORDER BY timestamp DESC, rowid DESC"
        params: tuple[object, ...] = (session_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (session_id, limit)
        return [_row_to_file_change(r) for r in self.conn.execute(sql, params).fetchall()]

    # ----- test runs -------------------------------------------------------

    def add_test_run(
        self,
        session_id: str,
        command: str,
        exit_code: int,
        *,
        passed: int | None = None,
        failed: int | None = None,
        skipped: int | None = None,
        failures: list[dict[str, str]] | None = None,
    ) -> TestRun:
        tid = new_id("run")
        now = utc_now()
        clean_command = sanitize(command)
        clean_failures = _sanitize_json((failures or [])[:MAX_STORED_FAILURES])
        try:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT INTO test_runs (id, session_id, command, exit_code, passed, failed, "
                    "skipped, failures, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        tid,
                        session_id,
                        clean_command,
                        exit_code,
                        passed,
                        failed,
                        skipped,
                        json.dumps(clean_failures),
                        now,
                    ),
                )
                conn.execute("UPDATE sessions SET last_updated = ? WHERE id = ?", (now, session_id))
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"Cannot store test run: {exc}") from exc
        return TestRun(
            id=tid,
            session_id=session_id,
            command=clean_command,
            exit_code=exit_code,
            passed=passed,
            failed=failed,
            skipped=skipped,
            failures=clean_failures,
            timestamp=now,
        )

    def last_test_run(self, session_id: str) -> TestRun | None:
        row = self.conn.execute(
            "SELECT * FROM test_runs WHERE session_id = ? ORDER BY timestamp DESC, rowid DESC "
            "LIMIT 1",
            (session_id,),
        ).fetchone()
        return _row_to_test_run(row) if row else None


def _split_sql(script: str) -> list[str]:
    """Split a migration script into statements (no semicolons inside our DDL literals)."""
    return [s.strip() for s in script.split(";") if s.strip()]
