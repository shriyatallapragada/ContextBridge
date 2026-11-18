"""`cb log`: sanitize and store a decision/note in the active session."""

from __future__ import annotations

from enum import StrEnum

import typer

from contextbridge.cli.common import console, error_boundary, log, open_workspace
from contextbridge.errors import UserError
from contextbridge.service import ProjectService


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
        note = ProjectService(ws.root).add_note(message, tag.value)
        decision, result = note.decision, note.redactions

        log.info("log: stored %s tag=%s redactions=%d", decision.id, tag.value, result.total)
        console.print(f"[bold green]✓[/] Logged [yellow]{tag.value}[/] [dim]({decision.id})[/]")
        if result.total:
            kinds = ", ".join(f"{k} x{n}" for k, n in sorted(result.redactions.items()))
            console.print(f"  [yellow]![/] Redacted {result.total} secret(s): {kinds}")
