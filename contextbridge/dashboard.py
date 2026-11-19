"""Deterministic dashboard operations: North Star intent, tasks, and git momentum.

This module is the single writer/reader for the v0.2.0 dashboard state. Everything in
it is plain SQLite and subprocess work (0 tokens); model calls stay behind the optional
file-explanation endpoints in the engine, which this module never touches.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from contextbridge.core.git_history import (
    DEFAULT_LIMIT,
    load_git_history,
    momentum_summary,
    sync_git_history,
)
from contextbridge.core.transcript_tasks import TaskDraft, parse_transcript_tasks
from contextbridge.errors import UserError
from contextbridge.service import ProjectService
from contextbridge.storage.db import DECISION_TAGS, Decision, utc_now
from contextbridge.storage.workspace import detect_branch
from contextbridge.synthesis.context_builder import logged_goal
from contextbridge.synthesis.facts import project_facts

if TYPE_CHECKING:
    from contextbridge.core.git_history import CommitRecord

TASK_STATUSES = ("open", "resolved", "dismissed", "superseded")


def ensure_service(root: Path) -> ProjectService:
    """The project service for ``root``, initializing storage on first use."""
    service = ProjectService.for_path(root)
    if not service.is_initialized():
        service.init(install_hook=False)
    return service


# ----- North Star intent -------------------------------------------------------


def get_intent(service: ProjectService) -> dict[str, Any]:
    with service.ws.open_db() as db:
        stored = db.get_intent()
    return {
        "north_star": stored[0] if stored else None,
        "updated_at": stored[1] if stored else None,
    }


def set_intent(service: ProjectService, text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise UserError("Write the project intent first.")
    with service.ws.open_db() as db:
        updated_at = db.set_intent(text)
    return {"north_star": text.strip(), "updated_at": updated_at}


# ----- notes (decisions of every tag) ----------------------------------


def list_notes(
    service: ProjectService, *, tag: str | None = None, limit: int = 200
) -> dict[str, Any]:
    """Logged notes of the active session, newest first, with per-tag counts."""
    if tag is not None and tag not in DECISION_TAGS:
        raise UserError(f"Invalid tag {tag!r}; expected one of: {', '.join(DECISION_TAGS)}.")
    with service.ws.open_db() as db:
        session, _ = service.ws.active_session(db)
        notes = db.list_decisions(session.id, limit=limit, tag=tag)
        counts = db.count_decisions_by_tag(session.id)
    return {
        "notes": [
            {"id": d.id, "content": d.content, "tag": d.tag, "created_at": d.created_at}
            for d in notes
        ],
        "counts": counts,
    }


# ----- tasks -------------------------------------------------------------------


def task_dict(decision: Decision) -> dict[str, Any]:
    return {
        "id": decision.id,
        "content": decision.content,
        "status": decision.status,
        "tag": decision.tag,
        "source": decision.source,
        "created_at": decision.created_at,
    }


def list_tasks(
    service: ProjectService,
    *,
    status: str | None = None,
    source: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    if status is not None and status not in TASK_STATUSES:
        raise UserError("Use open, resolved, dismissed or superseded.")
    with service.ws.open_db() as db:
        tasks = db.tasks(status=status, source=source, limit=limit)
        counts = db.task_counts()
    return {"tasks": [task_dict(t) for t in tasks], "counts": counts}


def add_task(
    service: ProjectService, content: str, *, source: str = "manual", tag: str = "todo"
) -> dict[str, Any]:
    clean = (content or "").strip()
    if not clean:
        raise UserError("The task is empty.")
    if tag not in DECISION_TAGS:
        raise UserError(f"Invalid tag {tag!r}; expected one of: {', '.join(DECISION_TAGS)}.")
    with service.ws.open_db() as db:
        session, _ = service.ws.active_session(db)
        decision = db.add_decision(session.id, clean, tag, source=source)
    return task_dict(decision)


def update_task(service: ProjectService, task_id: str, status: str) -> dict[str, Any]:
    if status not in TASK_STATUSES:
        raise UserError("Use open, resolved, dismissed or superseded.")
    with service.ws.open_db() as db:
        db.set_decision_status(task_id, status)
        row = db.conn.execute("SELECT * FROM decision_logs WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise UserError(f"No task with ID {task_id}.")
    return task_dict(
        Decision(
            id=row["id"],
            session_id=row["session_id"],
            content=row["content"],
            tag=row["tag"],
            created_at=row["created_at"],
            status=row["status"],
            superseded_by=row["superseded_by"],
            source=row["source"],
        )
    )


def remove_task(service: ProjectService, task_id: str) -> dict[str, Any]:
    with service.ws.open_db() as db:
        deleted = db.delete_decision(task_id)
    if not deleted:
        raise UserError(f"No task with ID {task_id}.")
    return {"id": task_id, "deleted": True}


# ----- git momentum ------------------------------------------------------------


def get_commits(
    service: ProjectService, *, limit: int = DEFAULT_LIMIT, refresh: bool = False
) -> dict[str, Any]:
    with service.ws.open_db() as db:
        commits = load_git_history(db, service.root, limit=limit, refresh=refresh)
    return {"commits": [c.to_dict() for c in commits], "momentum": momentum_summary(commits)}


def refresh_momentum(service: ProjectService, *, limit: int = DEFAULT_LIMIT) -> list[CommitRecord]:
    """Re-read the history window from git and persist it (used at server boot)."""
    with service.ws.open_db() as db:
        return sync_git_history(db, service.root, limit=limit)


# ----- Chat Bridge import ------------------------------------------------------


def _key(text: str) -> str:
    return " ".join(text.lower().split())


def preview_chat_tasks(text: str) -> list[dict[str, Any]]:
    """Deterministic transcript parse, without touching the database."""
    return [d.to_dict() for d in parse_transcript_tasks(text)]


def import_chat_tasks(
    service: ProjectService, text: str, *, commit: bool = True, source: str = "chat"
) -> dict[str, Any]:
    """Parse a pasted transcript into tasks; optionally store them immediately."""
    drafts: list[TaskDraft] = parse_transcript_tasks(text)
    with service.ws.open_db() as db:
        existing = {_key(t.content) for t in db.tasks(limit=500)}
        fresh = [d for d in drafts if _key(d.content) not in existing]
        created: list[dict[str, Any]] = []
        if commit and fresh:
            session, _ = service.ws.active_session(db)
            for draft in fresh:
                decision = db.add_decision(session.id, draft.content, "todo", source=source)
                if draft.status != decision.status:
                    db.set_decision_status(decision.id, draft.status)
                    decision = replace(decision, status=draft.status)
                item = task_dict(decision)
                item["line"] = draft.line
                created.append(item)
        counts = db.task_counts()
    return {
        "parsed": [d.to_dict() for d in drafts],
        "created": created,
        "duplicates": len(drafts) - len(fresh),
        "committed": bool(commit),
        "counts": counts,
    }


# ----- combined views ----------------------------------------------------------


def overview(service: ProjectService, *, refresh: bool = False) -> dict[str, Any]:
    """Everything the Overview tab needs, gathered deterministically in one call."""
    intent = get_intent(service)
    with service.ws.open_db() as db:
        goal = intent["north_star"]
        if not goal:
            session, _ = service.ws.active_session(db)
            goal = (
                logged_goal(db.list_decisions(session.id, tag="note"))
                or project_facts(service.root)[0]
            )
        counts = db.task_counts()
        commits = load_git_history(db, service.root, limit=DEFAULT_LIMIT, refresh=refresh)
    return {
        "project": service.name,
        "root": str(service.root),
        "branch": detect_branch(service.root),
        "initialized": service.is_initialized(),
        "generated_at": utc_now(),
        "north_star": intent["north_star"],
        "project_goal": goal or None,
        "north_star_updated_at": intent["updated_at"],
        "tasks": counts,
        "commits": [c.to_dict() for c in commits],
        "momentum": momentum_summary(commits),
    }


def project_status(service: ProjectService, *, open_limit: int = 20) -> dict[str, Any]:
    """Compact status block for the MCP ``get_project_status`` tool."""
    data = overview(service)
    with service.ws.open_db() as db:
        open_tasks = db.tasks(status="open", limit=open_limit)
        notes = db.conn.execute(
            "SELECT content, tag, created_at FROM decision_logs "
            "ORDER BY created_at DESC, rowid DESC LIMIT 5"
        ).fetchall()
    data["open_tasks"] = [task_dict(t) for t in open_tasks]
    data["recent_notes"] = [
        {"content": row["content"], "tag": row["tag"], "created_at": row["created_at"]}
        for row in notes
    ]
    return data
