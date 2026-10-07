"""`cb clear`: archive the session, delete all data, and/or remove injected agent files."""

from __future__ import annotations

import shutil

import typer
from rich.markup import escape

from contextbridge.adapters.registry import all_adapters
from contextbridge.cli.common import console, error_boundary, log, open_workspace
from contextbridge.errors import UserError
from contextbridge.storage.workspace import Workspace

VECTOR_DIRNAME = "vector_db"


def _archive_session(ws: Workspace) -> None:
    with ws.open_db() as db:
        state = ws.load_state()
        session = db.get_session(state.active_session_id) if state.active_session_id else None
        if session is None or session.status != "active":
            console.print("[dim]No active session to archive.[/]")
            return
        db.set_session_status(session.id, "archived")
        state.active_session_id = None
        state.active_branch = None
        ws.save_state(state)
    log.info("clear: archived %s", session.id)
    console.print(
        f"[bold green]✓[/] Archived session [magenta]{session.id}[/]. "
        "The next command starts a fresh one."
    )


def _delete_everything(ws: Workspace, *, yes: bool) -> None:
    with ws.open_db() as db:
        counts = db.count_all()
    vector_dir = ws.context_dir / VECTOR_DIRNAME
    summary = ", ".join(f"{n} {name.replace('_', ' ')}" for name, n in counts.items() if n)
    console.print(
        "[bold red]This permanently deletes[/] "
        + (escape(summary) if summary else "no rows")
        + (" and the vector index" if vector_dir.exists() else "")
        + ". Config and agent files are kept."
    )
    if not yes and not typer.confirm("Delete all ContextBridge session data?", default=False):
        console.print("Cancelled; nothing was deleted.")
        raise typer.Exit(1)
    with ws.open_db() as db:
        db.delete_all_sessions()
    state = ws.load_state()
    state.active_session_id = None
    state.active_branch = None
    state.last_handoff = None
    ws.save_state(state)
    if vector_dir.exists():
        shutil.rmtree(vector_dir, ignore_errors=True)
    log.info("clear: deleted all session data (%s)", summary or "empty")
    console.print("[bold green]✓[/] Deleted all session data.")


def _cleanup_adapters(ws: Workspace) -> None:
    removed = []
    for adapter in all_adapters(ws.root):
        if adapter.is_installed():
            adapter.cleanup()
            removed.append(adapter.relative_path)
    log.info("clear: adapter cleanup removed %s", removed)
    if removed:
        console.print(
            "[bold green]✓[/] Removed ContextBridge content from "
            + ", ".join(f"[cyan]{escape(p)}[/]" for p in removed)
        )
    else:
        console.print("[dim]No injected agent files found.[/]")


def clear_command(
    ctx: typer.Context,
    session: bool = typer.Option(
        False, "--session", help="Archive the active session (the default action)."
    ),
    all_data: bool = typer.Option(
        False, "--all", help="Permanently delete all sessions and their data."
    ),
    cleanup_adapters: bool = typer.Option(
        False, "--cleanup-adapters", help="Remove content ContextBridge injected into agent files."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask before deleting."),
) -> None:
    """Archive or delete session data, and optionally clean up injected agent files."""
    with error_boundary():
        if session and all_data:
            raise UserError("Use either --session or --all, not both.")
        ws = open_workspace(ctx)
        if all_data:
            _delete_everything(ws, yes=yes)
        elif session or not cleanup_adapters:
            _archive_session(ws)
        if cleanup_adapters:
            _cleanup_adapters(ws)
