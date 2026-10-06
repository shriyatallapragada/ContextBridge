"""`cb status`: dashboard of the active session (basic Phase 1 version)."""

from __future__ import annotations

from datetime import UTC, datetime

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from contextbridge.cli.common import console, error_boundary, open_workspace
from contextbridge.storage.db import DECISION_TAGS, parse_utc

RECENT_DECISIONS = 10
_TAG_STYLE = {
    "blocker": "bold red",
    "bug": "red",
    "architecture": "magenta",
    "todo": "yellow",
    "note": "white",
}


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


def status_command(ctx: typer.Context) -> None:
    """Show the active session and its recent decisions."""
    with error_boundary():
        ws = open_workspace(ctx)
        with ws.open_db() as db:
            state = ws.load_state()
            session = db.get_session(state.active_session_id) if state.active_session_id else None
            decisions = db.list_decisions(session.id, limit=RECENT_DECISIONS) if session else []
            counts = db.count_decisions_by_tag(session.id) if session else {}

        summary = Table.grid(padding=(0, 2))
        summary.add_column(style="bold")
        summary.add_column()
        summary.add_row("Project", escape(state.project_name))
        summary.add_row("Root", escape(str(ws.root)))
        if session is None:
            summary.add_row("Session", "[dim]none (run `cb log` or `cb init` to start one)[/]")
        else:
            summary.add_row("Branch", f"[cyan]{escape(session.branch_name)}[/]")
            started = _age(session.start_time)
            summary.add_row(
                "Session", f"[magenta]{session.id}[/] ({session.status}, started {started} ago)"
            )
            total = sum(counts.values())
            breakdown = ", ".join(f"{t} {counts[t]}" for t in DECISION_TAGS if counts.get(t))
            summary.add_row(
                "Decisions", f"{total}" + (f"  [dim]({breakdown})[/]" if breakdown else "")
            )
        last = state.last_handoff
        summary.add_row(
            "Last handoff",
            escape(f"{last.get('from')} → {last.get('to')} at {last.get('timestamp')}")
            if last
            else "[dim]none[/]",
        )
        console.print(Panel(summary, title="ContextBridge status", border_style="blue"))

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
        elif session is not None:
            console.print('[dim]No decisions logged yet. Try: cb log "..." --tag architecture[/]')
