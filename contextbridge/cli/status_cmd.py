"""`cb status`: dashboard of the active session."""

from __future__ import annotations

from datetime import UTC, datetime

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from contextbridge.cli.common import console, error_boundary, git_observer_for, open_workspace
from contextbridge.storage.db import DECISION_TAGS, parse_utc
from contextbridge.synthesis.context_builder import (
    SessionContext,
    build_context,
    describe_test_run,
    format_symbols,
)
from contextbridge.synthesis.token_counter import APPROX_LABEL

RECENT_DECISIONS = 10
CHANGED_FILES_SHOWN = 10
FAILURES_SHOWN = 5
_TAG_STYLE = {
    "blocker": "bold red",
    "bug": "red",
    "architecture": "magenta",
    "todo": "yellow",
    "note": "white",
}
_CHANGE_STYLE = {"added": "green", "deleted": "red", "renamed": "cyan", "modified": "yellow"}


def _age(timestamp: str) -> str:
    seconds = int((datetime.now(UTC) - parse_utc(timestamp)).total_seconds())
    seconds = max(seconds, 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600}h"


def _test_line(context: SessionContext) -> str:
    run = context.test_run
    if run is None:
        return "[dim]none (record one with `cb run -- <test command>`)[/]"
    style = "red" if context.tests_failing else "green"
    return f"[{style}]{escape(describe_test_run(run))}[/] [dim]{_age(run.timestamp)} ago[/]"


def _summary(context: SessionContext, state_last: dict[str, object] | None) -> Table:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold")
    grid.add_column()
    session = context.session
    grid.add_row("Project", escape(context.project))
    grid.add_row("Branch", f"[cyan]{escape(context.branch)}[/]")
    grid.add_row(
        "Session",
        f"[magenta]{session.id}[/] ({session.status}, started {_age(session.start_time)} ago)",
    )
    counts: dict[str, int] = {}
    for d in context.decisions:
        counts[d.tag] = counts.get(d.tag, 0) + 1
    breakdown = ", ".join(f"{t} {counts[t]}" for t in DECISION_TAGS if counts.get(t))
    grid.add_row(
        "Decisions", f"{len(context.decisions)}" + (f"  [dim]({breakdown})[/]" if breakdown else "")
    )
    grid.add_row("Commits", str(len(context.commits)))
    uncommitted = sum(f.uncommitted for f in context.files)
    total_files = len(context.files) + context.omitted_files
    grid.add_row(
        "Changed files",
        f"{total_files}" + (f"  [dim]({uncommitted} uncommitted)[/]" if uncommitted else ""),
    )
    grid.add_row("Last test run", _test_line(context))
    if state_last:
        ratio = str(state_last.get("compression_ratio", "n/a"))
        size = "larger than raw" if ratio.startswith("-") else f"{ratio} smaller"
        grid.add_row(
            "Last handoff",
            escape(
                f"{state_last.get('from')} → {state_last.get('to')} at "
                f"{state_last.get('timestamp')} ({state_last.get('method')}, {size})"
            ),
        )
    else:
        grid.add_row("Last handoff", "[dim]none[/]")
    grid.add_row("Context size", f"{context.raw_tokens:,} tokens raw [dim]({APPROX_LABEL})[/]")
    return grid


def status_command(ctx: typer.Context) -> None:
    """Show the active session: branch, changes, symbols, decisions, tests and tokens."""
    with error_boundary():
        ws = open_workspace(ctx)
        config = ws.load_config()
        state = ws.load_state()
        with ws.open_db() as db:
            session, _ = ws.active_session(db)
            context = build_context(
                db, session, config, project=state.project_name, observer=git_observer_for(ws)
            )
        console.print(
            Panel(
                _summary(context, ws.load_state().last_handoff),
                title="ContextBridge status",
                border_style="blue",
            )
        )
        console.print(
            f"[dim]Handoff budget: target {config.handoff.target_tokens:,}, hard cap "
            f"{config.handoff.hard_cap_tokens:,} tokens. Preview with `cb handoff --dry-run`.[/]"
        )

        if context.files:
            table = Table(title=f"Changed files (top {CHANGED_FILES_SHOWN})", title_justify="left")
            table.add_column("File", no_wrap=True)
            table.add_column("Change", no_wrap=True)
            table.add_column("Symbols")
            for f in context.files[:CHANGED_FILES_SHOWN]:
                style = _CHANGE_STYLE.get(f.change_type, "white")
                change = f"[{style}]{f.change_type}[/]" + (" [dim]*[/]" if f.uncommitted else "")
                symbols = format_symbols(f.symbols, limit=5) if f.symbols else "[dim]—[/]"
                table.add_row(escape(f.path), change, escape(symbols) if f.symbols else symbols)
            console.print(table)
            if any(f.uncommitted for f in context.files):
                console.print("[dim]* uncommitted[/]")

        run = context.test_run
        if run is not None and run.failures and context.tests_failing:
            console.print("[bold red]Failing tests[/]")
            for failure in run.failures[:FAILURES_SHOWN]:
                message = failure.get("message", "")
                console.print(
                    f"  • {escape(failure.get('test', '?'))}"
                    + (f" [dim]{escape(message)}[/]" if message else "")
                )

        decisions = context.decisions[:RECENT_DECISIONS]
        if decisions:
            table = Table(title=f"Recent decisions (last {RECENT_DECISIONS})", title_justify="left")
            table.add_column("When", style="dim", no_wrap=True)
            table.add_column("Tag", no_wrap=True)
            table.add_column("Content")
            for d in decisions:
                style = _TAG_STYLE.get(d.tag, "white")
                table.add_row(
                    f"{_age(d.created_at)} ago", f"[{style}]{d.tag}[/]", escape(d.content)
                )
            console.print(table)
        else:
            console.print('[dim]No decisions logged yet. Try: cb log "..." --tag architecture[/]')
