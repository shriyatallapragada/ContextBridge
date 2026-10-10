"""`cb clear`: archive the session, delete all data, and/or remove injected agent files."""

from __future__ import annotations

import typer
from rich.markup import escape

from contextbridge.cli.common import console, error_boundary, open_workspace
from contextbridge.errors import UserError
from contextbridge.service import ProjectService


def _archive_session(service: ProjectService) -> None:
    session = service.archive_session()
    if session is None:
        console.print("[dim]No active session to archive.[/]")
        return
    console.print(
        f"[bold green]✓[/] Archived session [magenta]{session.id}[/]. "
        "The next command starts a fresh one."
    )


def _delete_everything(service: ProjectService, *, yes: bool) -> None:
    counts = service.count_all()
    summary = ", ".join(f"{n} {name.replace('_', ' ')}" for name, n in counts.items() if n)
    console.print(
        "[bold red]This permanently deletes[/] "
        + (escape(summary) if summary else "no rows")
        + (" and the vector index" if service.has_vector_index() else "")
        + ". Config and agent files are kept."
    )
    if not yes and not typer.confirm("Delete all ContextBridge session data?", default=False):
        console.print("Cancelled; nothing was deleted.")
        raise typer.Exit(1)
    service.delete_all()
    console.print("[bold green]✓[/] Deleted all session data.")


def _cleanup_adapters(service: ProjectService) -> None:
    removed = service.cleanup_adapters()
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
        service = ProjectService(open_workspace(ctx).root)
        if all_data:
            _delete_everything(service, yes=yes)
        elif session or not cleanup_adapters:
            _archive_session(service)
        if cleanup_adapters:
            _cleanup_adapters(service)
