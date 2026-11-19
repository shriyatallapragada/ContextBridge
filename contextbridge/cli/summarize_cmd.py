"""Explicit project summarization and todo lifecycle commands."""

from __future__ import annotations

import typer
from rich.markup import escape

from contextbridge.cli.common import (
    app_context,
    console,
    err_console,
    error_boundary,
    open_workspace,
)
from contextbridge.service import ProjectService


def summarize_command(
    ctx: typer.Context,
    budget: int = typer.Option(2000, "--budget", min=200),
    no_llm: bool = typer.Option(False, "--no-llm"),
    explain: bool | None = typer.Option(
        None, "--explain/--no-explain", help="Optional AI-written beginner explanations."
    ),
    refresh: bool = typer.Option(
        False, "--refresh", help="Rebuild cached summaries within request limits."
    ),
) -> None:
    """Summarize a project, creating local cache storage if necessary (no git hooks)."""
    with error_boundary():
        service = ProjectService.for_path(app_context(ctx).start_dir)
        result = service.summarize(
            no_llm=no_llm,
            refresh=refresh,
            explain=explain,
            notify=lambda message: err_console.print(escape(message)),
        )
        typer.echo(result.render(budget))


todo_app = typer.Typer(
    help="List, resolve, dismiss or supersede logged todos.", no_args_is_help=True
)


@todo_app.command("list")
def todo_list(ctx: typer.Context, all_items: bool = typer.Option(False, "--all")) -> None:
    with error_boundary():
        ws = open_workspace(ctx)
        with ws.open_db() as db:
            rows = db.conn.execute(
                "SELECT id, content, status FROM decision_logs WHERE tag='todo' "
                + ("" if all_items else "AND status='open' ")
                + "ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        for row in rows:
            console.print(escape(f"{row['id']} [{row['status']}] {row['content']}"))
        if not rows:
            console.print("No matching todos.")


def _status(ctx: typer.Context, item: str, status: str, replacement: str | None = None) -> None:
    with error_boundary():
        ws = open_workspace(ctx)
        with ws.open_db() as db:
            db.set_decision_status(item, status, superseded_by=replacement)
        console.print(f"Logged item {item}: {status}.")


@todo_app.command("resolve")
def todo_resolve(ctx: typer.Context, item: str) -> None:
    """Mark an item completed, retaining its history."""
    _status(ctx, item, "resolved")


@todo_app.command("dismiss")
def todo_dismiss(ctx: typer.Context, item: str) -> None:
    """Hide an obsolete item without claiming it was completed."""
    _status(ctx, item, "dismissed")


@todo_app.command("supersede")
def todo_supersede(ctx: typer.Context, item: str, replacement: str) -> None:
    """Replace a logged item with a newer item by ID; retain both records."""
    _status(ctx, item, "superseded", replacement)
