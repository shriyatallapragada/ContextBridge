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
from typing import TYPE_CHECKING, Any, Final

from contextbridge.core.sanitizer import sanitize
from contextbridge.errors import StorageError, UserError

if TYPE_CHECKING:
    from contextbridge.core.git_history import CommitRecord
    from contextbridge.core.git_observer import FileDiff


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
_MIGRATION_3: Final = """
CREATE TABLE IF NOT EXISTS change_batches (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  timestamp TEXT NOT NULL,
  duration_seconds REAL NOT NULL,
  event_count INTEGER NOT NULL,
  files TEXT NOT NULL,
  summary TEXT NOT NULL,
  agent TEXT,
  notes TEXT NOT NULL
);
"""
_MIGRATION_4: Final = """
CREATE TABLE file_summaries (
  path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  profile TEXT NOT NULL,
  chunk_index INTEGER NOT NULL DEFAULT -1,
  summary_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(path, content_hash, profile, chunk_index)
);
ALTER TABLE decision_logs ADD COLUMN status TEXT NOT NULL DEFAULT 'open';
ALTER TABLE decision_logs ADD COLUMN superseded_by TEXT;
"""
_MIGRATION_5: Final = """
CREATE TABLE file_explanations (
 path TEXT NOT NULL, content_hash TEXT NOT NULL, profile TEXT NOT NULL,
 explanation TEXT NOT NULL, mentioned_names TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(path, content_hash, profile)
);
CREATE TABLE provider_limits (profile TEXT PRIMARY KEY, state_json TEXT NOT NULL);
"""
_MIGRATION_6: Final = """
CREATE TABLE component_explanations (
 path TEXT NOT NULL, content_hash TEXT NOT NULL, profile TEXT NOT NULL,
 summary_json TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(path, content_hash, profile)
);
"""
# v7 (v0.2.0): the persistent North Star intent, the boot-time git momentum window,
# and provenance for rows imported from a pasted chat transcript.
_MIGRATION_7: Final = """
CREATE TABLE project_intent (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  north_star TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE git_history (
  sha          TEXT PRIMARY KEY,
  short_sha    TEXT NOT NULL,
  author       TEXT NOT NULL,
  committed_at TEXT NOT NULL,
  subject      TEXT NOT NULL,
  files        TEXT NOT NULL,
  insertions   INTEGER NOT NULL DEFAULT 0,
  deletions    INTEGER NOT NULL DEFAULT 0,
  parsed_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_git_history_time ON git_history(committed_at);
ALTER TABLE decision_logs ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
"""
MIGRATIONS: Final[tuple[str, ...]] = (
    _MIGRATION_1,
    _MIGRATION_2,
    _MIGRATION_3,
    _MIGRATION_4,
    _MIGRATION_5,
    _MIGRATION_6,
    _MIGRATION_7,
)
SCHEMA_VERSION: Final = len(MIGRATIONS)


def new_id(prefix: str) -> str:
    """Return an ID like ``sess_a1b2c3d4e5f6``."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utc_now(*, precise: bool = False) -> str:
    """Current time as a UTC ISO-8601 string (``2026-10-06T21:00:00Z``)."""
    return (
        datetime.now(UTC)
        .isoformat(timespec="microseconds" if precise else "seconds")
        .replace("+00:00", "Z")
    )


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
    status: str = "open"
    superseded_by: str | None = None
    source: str = "manual"


@dataclass(frozen=True)
class HandoffRecord:
    id: str
    session_id: str | None
    from_agent: str
    to_agent: str
    raw_token_count: int | None
    compressed_token_count: int | None
    method: str | None
    payload_content: str
    written_path: str | None
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
    line_delta: tuple[int, int] | None = None


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
        status=row["status"],
        superseded_by=row["superseded_by"],
        source=row["source"],
    )


def _row_to_handoff(row: sqlite3.Row) -> HandoffRecord:
    return HandoffRecord(
        id=row["id"],
        session_id=row["session_id"],
        from_agent=row["from_agent"],
        to_agent=row["to_agent"],
        raw_token_count=row["raw_token_count"],
        compressed_token_count=row["compressed_token_count"],
        method=row["method"],
        payload_content=row["payload_content"],
        written_path=row["written_path"],
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


def _row_to_commit(row: sqlite3.Row) -> CommitRecord:
    from contextbridge.core.git_history import ChangedFile, CommitRecord

    try:
        raw_files = json.loads(row["files"])
    except json.JSONDecodeError:
        raw_files = []
    files = [
        ChangedFile(path=str(item.get("path", "")), lines=int(item.get("lines", 0)))
        for item in raw_files
        if isinstance(item, dict)
    ]
    return CommitRecord(
        sha=row["sha"],
        short_sha=row["short_sha"],
        author=row["author"],
        committed_at=row["committed_at"],
        subject=row["subject"],
        files=files,
        insertions=int(row["insertions"]),
        deletions=int(row["deletions"]),
        parsed_at=row["parsed_at"],
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

    def add_decision(
        self, session_id: str, content: str, tag: str = "note", *, source: str = "manual"
    ) -> Decision:
        if tag not in DECISION_TAGS:
            raise UserError(f"Invalid tag {tag!r}; expected one of: {', '.join(DECISION_TAGS)}.")
        clean = sanitize(content).strip()
        if not clean:
            raise UserError("Log message is empty.")
        clean_source = sanitize(source).strip()[:40] or "manual"
        did = new_id("dec")
        now = utc_now()
        try:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT INTO decision_logs (id, session_id, content, tag, created_at, source) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (did, session_id, clean, tag, now, clean_source),
                )
                conn.execute("UPDATE sessions SET last_updated = ? WHERE id = ?", (now, session_id))
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"Cannot store decision: {exc}") from exc
        return Decision(
            id=did,
            session_id=session_id,
            content=clean,
            tag=tag,
            created_at=now,
            source=clean_source,
        )

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

    def set_decision_status(
        self, decision_id: str, status: str, *, superseded_by: str | None = None
    ) -> None:
        if status not in {"open", "resolved", "dismissed", "superseded"}:
            raise UserError("Use open, resolved, dismissed or superseded.")
        if not self.conn.execute(
            "SELECT 1 FROM decision_logs WHERE id=?", (decision_id,)
        ).fetchone():
            raise UserError(f"No logged item with ID {decision_id}.")
        if status == "superseded" and (
            not superseded_by
            or decision_id == superseded_by
            or not self.conn.execute(
                "SELECT 1 FROM decision_logs WHERE id=? AND status='open'", (superseded_by,)
            ).fetchone()
        ):
            raise UserError("Choose a different open logged item as the replacement.")
        self.conn.execute(
            "UPDATE decision_logs SET status=?, superseded_by=? WHERE id=?",
            (status, superseded_by, decision_id),
        )

    def delete_decision(self, decision_id: str) -> bool:
        """Remove one logged item (used by the dashboard checklist). False if unknown."""
        cur = self.conn.execute("DELETE FROM decision_logs WHERE id = ?", (decision_id,))
        return cur.rowcount > 0

    def tasks(
        self, *, status: str | None = None, source: str | None = None, limit: int = 500
    ) -> list[Decision]:
        """Logged to-dos, newest first, optionally filtered by status or provenance."""
        sql = "SELECT * FROM decision_logs WHERE tag='todo'"
        params: list[object] = []
        if status is not None:
            if status not in {"open", "resolved", "dismissed", "superseded"}:
                raise UserError("Use open, resolved, dismissed or superseded.")
            sql += " AND status=?"
            params.append(status)
        if source is not None:
            sql += " AND source=?"
            params.append(sanitize(source)[:40])
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        return [_row_to_decision(r) for r in self.conn.execute(sql, params).fetchall()]

    def task_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM decision_logs WHERE tag='todo' GROUP BY status"
        ).fetchall()
        counts = {status: 0 for status in ("open", "resolved", "dismissed", "superseded")}
        counts.update({r["status"]: int(r["n"]) for r in rows})
        counts["total"] = sum(counts[s] for s in ("open", "resolved", "dismissed", "superseded"))
        return counts

    # ----- project intent (North Star) -------------------------------------

    def get_intent(self) -> tuple[str, str] | None:
        """The persistent project intent as ``(north_star, updated_at)``, if set."""
        row = self.conn.execute(
            "SELECT north_star, updated_at FROM project_intent WHERE id = 1"
        ).fetchone()
        return (str(row[0]), str(row[1])) if row else None

    def set_intent(self, text: str) -> str:
        """Store the single-row North Star intent; returns its update timestamp."""
        clean = sanitize(text).strip()
        if not clean:
            raise UserError("Project intent is empty.")
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO project_intent (id, north_star, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET north_star=excluded.north_star, "
                "updated_at=excluded.updated_at",
                (clean, now),
            )
        return now

    # ----- git momentum -----------------------------------------------------

    def replace_git_commits(self, commits: list[CommitRecord]) -> None:
        """Store exactly the parsed history window; older rows fall out."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM git_history")
            for commit in commits:
                conn.execute(
                    "INSERT INTO git_history (sha, short_sha, author, committed_at, subject, "
                    "files, insertions, deletions, parsed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        commit.sha,
                        commit.short_sha,
                        commit.author,
                        commit.committed_at,
                        commit.subject,
                        json.dumps(
                            [{"path": f.path, "lines": f.lines} for f in commit.files],
                            ensure_ascii=False,
                        ),
                        commit.insertions,
                        commit.deletions,
                        commit.parsed_at,
                    ),
                )

    def list_git_commits(self, limit: int = 15) -> list[CommitRecord]:
        # Rows are replaced wholesale in `git log` order (newest first), so rowid order
        # *is* the parsed order, including for commits sharing a timestamp.
        rows = self.conn.execute(
            "SELECT * FROM git_history ORDER BY rowid LIMIT ?",
            (max(1, min(limit, 100)),),
        ).fetchall()
        return [_row_to_commit(row) for row in rows]

    def older_work(self, session_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT s.id, s.branch_name, COUNT(d.id) AS n, MAX(d.content) AS example "
            "FROM sessions s JOIN decision_logs d ON d.session_id=s.id "
            "WHERE s.id != ? AND d.tag='note' AND d.status='open' "
            "AND lower(d.content) NOT LIKE 'goal:%' AND lower(d.content) NOT LIKE 'next:%' "
            "GROUP BY s.id ORDER BY s.start_time DESC LIMIT 5",
            (session_id,),
        ).fetchall()
        return [
            sanitize(
                f"Earlier session {r['branch_name']}: {r['n']} recorded notes; "
                + r["example"].split(". ", 1)[0][:180]
                + "."
            )
            for r in rows
        ]

    def cached_summary(
        self, path: str, content_hash: str, profile: str, chunk_index: int = -1
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT summary_json FROM file_summaries "
            "WHERE path=? AND content_hash=? AND profile=? AND chunk_index=?",
            (path, content_hash, profile, chunk_index),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def cache_summary(
        self,
        path: str,
        content_hash: str,
        profile: str,
        summary: dict[str, Any],
        chunk_index: int = -1,
    ) -> None:
        # Only derived summaries are stored, never source chunks or prompts.
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM file_summaries WHERE path=? AND content_hash!=?", (path, content_hash)
            )
            conn.execute(
                "INSERT OR REPLACE INTO file_summaries VALUES (?, ?, ?, ?, ?, ?)",
                (
                    path,
                    content_hash,
                    profile,
                    chunk_index,
                    json.dumps(_sanitize_json(summary)),
                    utc_now(),
                ),
            )

    def file_summaries(self) -> list[dict[str, Any]]:
        """Reusable labels and imports for diagram consumers; source code is never stored."""
        rows = self.conn.execute(
            "SELECT path, content_hash, profile, summary_json FROM file_summaries "
            "WHERE chunk_index=-1 ORDER BY updated_at DESC, rowid DESC"
        ).fetchall()
        return [
            {**json.loads(r[3]), "path": r[0], "content_hash": r[1], "profile": r[2]} for r in rows
        ]

    def cache_explanation(
        self, path: str, content_hash: str, profile: str, text: str, names: list[str]
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM file_explanations WHERE path=? AND content_hash!=?",
                (path, content_hash),
            )
            conn.execute(
                "INSERT OR REPLACE INTO file_explanations VALUES (?,?,?,?,?,?)",
                (
                    path,
                    content_hash,
                    profile,
                    sanitize(text),
                    json.dumps(_sanitize_json(names)),
                    utc_now(),
                ),
            )

    def explanation(self, path: str, content_hash: str, profile: str) -> str:
        row = self.conn.execute(
            "SELECT explanation FROM file_explanations "
            "WHERE path=? AND content_hash=? AND profile=?",
            (path, content_hash, profile),
        ).fetchone()
        return row[0] if row else ""

    def file_explanations(self) -> list[dict[str, str]]:
        return [
            dict(r) | {"label": "AI-written"}
            for r in self.conn.execute(
                "SELECT path, content_hash, profile, explanation FROM file_explanations "
                "ORDER BY updated_at DESC, rowid DESC"
            ).fetchall()
        ]

    def component_explanations(self) -> list[dict[str, Any]]:
        return [
            dict(json.loads(r[3]), path=r[0], content_hash=r[1], profile=r[2])
            for r in self.conn.execute(
                "SELECT path, content_hash, profile, summary_json FROM component_explanations "
                "ORDER BY updated_at DESC, rowid DESC"
            ).fetchall()
        ]

    def cache_component(self, path: str, digest: str, profile: str, result: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO component_explanations VALUES (?,?,?,?,?)",
            (path, digest, profile, json.dumps(_sanitize_json(result)), utc_now()),
        )

    def save_limits(self, profile: str, state: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO provider_limits VALUES (?,?)", (profile, json.dumps(state))
        )

    def load_limits(self, profile: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT state_json FROM provider_limits WHERE profile=?", (profile,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    # ----- handoffs --------------------------------------------------------

    def add_handoff(
        self,
        session_id: str,
        from_agent: str,
        to_agent: str,
        *,
        raw_tokens: int,
        compressed_tokens: int,
        method: str,
        payload: str,
        written_path: str | None,
    ) -> HandoffRecord:
        hid = new_id("hand")
        now = utc_now()
        record = HandoffRecord(
            id=hid,
            session_id=session_id,
            from_agent=sanitize(from_agent),
            to_agent=sanitize(to_agent),
            raw_token_count=raw_tokens,
            compressed_token_count=compressed_tokens,
            method=method,
            payload_content=sanitize(payload),
            written_path=sanitize(written_path) if written_path else None,
            created_at=now,
        )
        try:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT INTO handoffs (id, session_id, from_agent, to_agent, raw_token_count, "
                    "compressed_token_count, method, payload_content, written_path, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.id,
                        record.session_id,
                        record.from_agent,
                        record.to_agent,
                        record.raw_token_count,
                        record.compressed_token_count,
                        record.method,
                        record.payload_content,
                        record.written_path,
                        record.created_at,
                    ),
                )
                conn.execute("UPDATE sessions SET last_updated = ? WHERE id = ?", (now, session_id))
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"Cannot store handoff: {exc}") from exc
        return record

    def list_handoffs(self, session_id: str | None = None) -> list[HandoffRecord]:
        sql = "SELECT * FROM handoffs"
        params: tuple[object, ...] = ()
        if session_id is not None:
            sql += " WHERE session_id = ?"
            params = (session_id,)
        sql += " ORDER BY created_at DESC, rowid DESC"
        return [_row_to_handoff(r) for r in self.conn.execute(sql, params).fetchall()]

    # ----- clearing --------------------------------------------------------

    def count_all(self) -> dict[str, int]:
        """Row counts of everything `cb clear --all` would delete."""
        tables = (
            "sessions",
            "decision_logs",
            "file_changes",
            "test_runs",
            "handoffs",
            "change_batches",
        )
        return {
            t: int(self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables
        }

    def delete_all_sessions(self) -> dict[str, int]:
        """Delete every session and everything attached to it. Returns the counts deleted."""
        counts = self.count_all()
        with self.transaction() as conn:
            # handoffs.session_id has no ON DELETE action, so they go first.
            conn.execute("DELETE FROM handoffs")
            conn.execute("DELETE FROM sessions")  # cascades to decisions/changes/test runs
        return counts

    def add_commit_changes(self, session_id: str, sha: str, files: list[FileDiff]) -> int:
        """All files of a commit are recorded together, including on concurrent retries."""
        now = utc_now(precise=True)
        with self.transaction() as conn:
            if self.has_commit(sha):
                return 0
            for item in files:
                conn.execute(
                    "INSERT INTO file_changes (id, session_id, commit_sha, file_path, change_type, "
                    "symbols_modified, diff_snippet, timestamp) VALUES (?, ?, ?, ?, ?, '[]', ?, ?)",
                    (
                        new_id("chg"),
                        session_id,
                        sha,
                        sanitize(item.path),
                        item.change_type.value,
                        item.snippet,
                        now,
                    ),
                )
            conn.execute("UPDATE sessions SET last_updated = ? WHERE id = ?", (now, session_id))
        return len(files)

    def add_change_batch(
        self,
        session_id: str,
        files: list[dict[str, str]],
        duration: float,
        event_count: int,
        agent: str | None = None,
        notes: str = "",
    ) -> str:
        """Atomically store a batch and path/summary rows for existing context readers."""
        batch_id, timestamp = new_id("batch"), utc_now(precise=True)
        clean = _sanitize_json(files)
        summary = f"{len(files)} files changed in {duration:.1f}s"
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO change_batches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    batch_id,
                    session_id,
                    timestamp,
                    duration,
                    event_count,
                    json.dumps(clean),
                    summary,
                    sanitize(agent) if agent else None,
                    sanitize(notes),
                ),
            )
            for item in clean:
                conn.execute(
                    "INSERT INTO file_changes "
                    "(id, session_id, file_path, change_type, symbols_modified, "
                    "diff_snippet, timestamp) "
                    "VALUES (?, ?, ?, ?, '[]', ?, ?)",
                    (
                        new_id("chg"),
                        session_id,
                        item["path"],
                        item["change_type"],
                        item["summary"],
                        timestamp,
                    ),
                )
            conn.execute(
                "UPDATE sessions SET last_updated = ? WHERE id = ?", (timestamp, session_id)
            )
        return summary

    # ----- file changes ----------------------------------------------------

    def has_commit(self, commit_sha: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM file_changes WHERE commit_sha = ? LIMIT 1", (commit_sha,)
        ).fetchone()
        return row is not None

    def session_commit_shas(self, session_id: str) -> list[str]:
        """Distinct commits recorded for a session, oldest first."""
        rows = self.conn.execute(
            "SELECT commit_sha, MIN(rowid) AS first FROM file_changes "
            "WHERE session_id = ? AND commit_sha IS NOT NULL GROUP BY commit_sha ORDER BY first",
            (session_id,),
        ).fetchall()
        return [str(r[0]) for r in rows]

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
        now = utc_now(precise=True)
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
        now = utc_now(precise=True)
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


@dataclass(frozen=True)
class SearchHit:
    kind: str  # "decision" | "file_change"
    id: str
    session_id: str
    text: str
    timestamp: str
    score: float | None = None  # similarity for vector hits; None for keyword hits


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def keyword_search(db: Database, query: str, *, limit: int = 10) -> list[SearchHit]:
    """Case-insensitive substring search over decisions and file changes (all sessions)."""
    pattern = _like_pattern(query)
    rows = db.conn.execute(
        "SELECT 'decision' AS kind, id, session_id, '[' || tag || '] ' || content AS text, "
        "created_at AS ts FROM decision_logs WHERE content LIKE :p ESCAPE '\\' "
        "UNION ALL "
        "SELECT 'file_change', id, session_id, file_path || ' ' || COALESCE(symbols_modified, '') "
        "|| char(10) || COALESCE(diff_snippet, ''), timestamp FROM file_changes "
        "WHERE file_path LIKE :p ESCAPE '\\' OR symbols_modified LIKE :p ESCAPE '\\' "
        "OR diff_snippet LIKE :p ESCAPE '\\' "
        "ORDER BY ts DESC LIMIT :n",
        {"p": pattern, "n": limit},
    ).fetchall()
    return [SearchHit(kind=r[0], id=r[1], session_id=r[2], text=r[3], timestamp=r[4]) for r in rows]


def _split_sql(script: str) -> list[str]:
    """Split a migration script into statements (no semicolons inside our DDL literals)."""
    return [s.strip() for s in script.split(";") if s.strip()]
