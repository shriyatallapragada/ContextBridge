"""The `.context/` workspace: discovery, creation, `.gitignore`, `state.json`, logging."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextbridge.config import CONFIG_FILENAME, Config, load_config, save_config
from contextbridge.core.sanitizer import sanitize
from contextbridge.errors import NotInitializedError, StorageError
from contextbridge.storage.db import Database, Session

CONTEXT_DIRNAME = ".context"
DB_FILENAME = "state.db"
STATE_FILENAME = "state.json"
LOG_FILENAME = "cb.log"
STATE_SCHEMA_VERSION = 1
UNKNOWN_BRANCH = "(no-branch)"

_GITIGNORE_ENTRY = ".context/"
_GITIGNORE_EQUIVALENTS = {".context", ".context/", "/.context", "/.context/", ".context/**"}

log = logging.getLogger("contextbridge")


def find_project_root(start: Path) -> Path:
    """Return the nearest ancestor of ``start`` containing ``.git``, else ``start`` itself."""
    start = start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def find_context_dir(start: Path) -> Path:
    """Walk up from ``start`` (like git) to find ``.context/``; raise if not initialized."""
    start = start.resolve()
    for candidate in (start, *start.parents):
        ctx = candidate / CONTEXT_DIRNAME
        if (ctx / DB_FILENAME).is_file():
            return ctx
    raise NotInitializedError(str(start))


def detect_branch(repo_root: Path) -> str:
    """Best-effort current branch name. Full git handling arrives with the git observer."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN_BRANCH
    branch = proc.stdout.strip()
    return branch if proc.returncode == 0 and branch else UNKNOWN_BRANCH


def ensure_gitignored(repo_root: Path) -> bool:
    """Add ``.context/`` to ``<repo_root>/.gitignore`` if missing. Returns True if changed."""
    path = repo_root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if any(line.strip() in _GITIGNORE_EQUIVALENTS for line in existing.splitlines()):
        return False
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{prefix}{_GITIGNORE_ENTRY}\n")
    return True


@dataclass
class State:
    """Contents of ``.context/state.json``."""

    project_name: str
    active_session_id: str | None = None
    active_branch: str | None = None
    last_handoff: dict[str, Any] | None = None
    schema_version: int = STATE_SCHEMA_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = dict(self.extra)
        data.update(
            schema_version=self.schema_version,
            project_name=self.project_name,
            active_session_id=self.active_session_id,
            active_branch=self.active_branch,
            last_handoff=self.last_handoff,
        )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_project: str) -> State:
        known = {
            "schema_version",
            "project_name",
            "active_session_id",
            "active_branch",
            "last_handoff",
        }
        last = data.get("last_handoff")
        return cls(
            project_name=str(data.get("project_name") or default_project),
            active_session_id=data.get("active_session_id"),
            active_branch=data.get("active_branch"),
            last_handoff=last if isinstance(last, dict) else None,
            schema_version=int(data.get("schema_version", STATE_SCHEMA_VERSION)),
            extra={k: v for k, v in data.items() if k not in known},
        )


@dataclass
class Workspace:
    """A located (or freshly created) ``.context/`` directory."""

    root: Path

    @property
    def context_dir(self) -> Path:
        return self.root / CONTEXT_DIRNAME

    @property
    def db_path(self) -> Path:
        return self.context_dir / DB_FILENAME

    @property
    def state_path(self) -> Path:
        return self.context_dir / STATE_FILENAME

    @property
    def config_path(self) -> Path:
        return self.context_dir / CONFIG_FILENAME

    @property
    def log_path(self) -> Path:
        return self.context_dir / LOG_FILENAME

    # ----- discovery / creation -------------------------------------------

    @classmethod
    def locate(cls, start: Path) -> Workspace:
        return cls(root=find_context_dir(start).parent)

    @classmethod
    def create(cls, root: Path) -> tuple[Workspace, bool]:
        """Create ``.context/`` under ``root``. Returns (workspace, created_dir). Idempotent."""
        ws = cls(root=root.resolve())
        created = not ws.context_dir.is_dir()
        ws.context_dir.mkdir(parents=True, exist_ok=True)
        if not ws.config_path.exists():
            # Empty per-project override: global config applies until the user edits it.
            save_config({}, ws.config_path)
        return ws, created

    def open_db(self, *, migrate: bool = True) -> Database:
        return Database.open(self.db_path, migrate=migrate)

    def load_config(self) -> Config:
        return load_config(self.context_dir)

    # ----- state.json ------------------------------------------------------

    def load_state(self) -> State:
        default_project = self.root.name
        if not self.state_path.is_file():
            return State(project_name=default_project)
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(
                f"Cannot read {self.state_path}: {exc}",
                hint="Delete the file and re-run `cb init` to recreate it.",
            ) from exc
        if not isinstance(data, dict):
            raise StorageError(f"{self.state_path} must contain a JSON object.")
        return State.from_dict(data, default_project)

    def save_state(self, state: State) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    # ----- sessions ----------------------------------------------------------

    def resolve_active_session(self, db: Database, state: State) -> tuple[Session, bool]:
        """Return the active session (creating one if needed) and whether it was created.

        Prefers the session recorded in ``state.json``; otherwise resumes the active
        session for the current branch, or starts a new one. Updates ``state`` in place.
        """
        session = db.get_session(state.active_session_id) if state.active_session_id else None
        created = False
        if session is None or session.status != "active":
            branch = detect_branch(self.root)
            session = db.find_active_session(branch)
            if session is None:
                session = db.create_session(branch)
                created = True
        state.active_session_id = session.id
        state.active_branch = session.branch_name
        return session, created

    # ----- logging -----------------------------------------------------------

    def setup_logging(self, *, verbose: bool = False) -> None:
        """Attach a sanitizing file handler for ``.context/cb.log`` (once per process)."""
        target = str(self.log_path)
        for handler in list(log.handlers):
            if isinstance(handler, logging.FileHandler):
                if handler.baseFilename == target:
                    return
                log.removeHandler(handler)
                handler.close()
        handler = logging.FileHandler(target, encoding="utf-8")
        handler.setFormatter(_SanitizingFormatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.DEBUG if verbose else logging.INFO)


class _SanitizingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return sanitize(super().format(record))
