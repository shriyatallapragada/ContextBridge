"""`cb log`: sanitize and store a decision/note in the active session."""

from __future__ import annotations

from enum import StrEnum

import typer

from contextbridge.cli.common import console, error_boundary, log, open_workspace
from contextbridge.core.sanitizer import sanitize_with_report
from contextbridge.errors import UserError


class Tag(StrEnum):
    note = "note"
    bug = "bug"
    architecture = "architecture"
    blocker = "blocker"
    todo = "todo"


def log_command(
    ctx: typer.Context,
    message: str = typer.Argument(..., help="The note or decision to record."),
    tag: Tag = typer.Option(Tag.note, "--tag", "-t", help="Category of the entry."),
) -> None:
    """Record a note, decision, bug, blocker or todo for the current session."""
    with error_boundary():
        if not message.strip():
            raise UserError("Log message is empty.")
        ws = open_workspace(ctx)
        result = sanitize_with_report(message)
        with ws.open_db() as db:
            session, _ = ws.active_session(db)
            decision = db.add_decision(session.id, result.text, tag.value)

        log.info("log: stored %s tag=%s redactions=%d", decision.id, tag.value, result.total)
        console.print(f"[bold green]✓[/] Logged [yellow]{tag.value}[/] [dim]({decision.id})[/]")
        if result.total:
            kinds = ", ".join(f"{k} x{n}" for k, n in sorted(result.redactions.items()))
            console.print(f"  [yellow]![/] Redacted {result.total} secret(s): {kinds}")
