"""`cb handoff`: build the session context, compress it, and preview the payload.

Writing the payload into an agent's files (adapters), recording it in `handoffs`, and
updating `last_handoff` are not available yet; `--dry-run` previews everything else.
"""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from contextbridge.cli.common import (
    console,
    err_console,
    error_boundary,
    git_observer_for,
    log,
    open_workspace,
)
from contextbridge.errors import UserError
from contextbridge.synthesis.compressor import DETERMINISTIC, Handoff, build_handoff, format_ratio
from contextbridge.synthesis.context_builder import build_context
from contextbridge.synthesis.token_counter import APPROX_LABEL


def _stats_table(handoff: Handoff, cap: int) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    method = handoff.method
    failed = [a for a in handoff.attempts if a.outcome not in ("ok", "retried", "truncated")]
    if method == DETERMINISTIC and failed:
        reasons = "; ".join(f"{a.provider}: {a.outcome}" for a in failed)
        method += f" [dim](fallback: {escape(reasons)})[/]"
    elif any(a.outcome in ("retried", "truncated") for a in handoff.attempts):
        method += f" [dim]({handoff.attempts[-1].outcome} to fit)[/]"
    table.add_row("Method", method)
    table.add_row("Raw context", f"{handoff.raw_tokens:,} tokens ({APPROX_LABEL})")
    table.add_row(
        "Payload", f"{handoff.compressed_tokens:,} tokens ({APPROX_LABEL}, hard cap {cap:,})"
    )
    table.add_row("Compression", format_ratio(handoff.compression_ratio))
    return table


def handoff_command(
    ctx: typer.Context,
    from_agent: str | None = typer.Option(None, "--from", help="Agent you are leaving."),
    to_agent: str | None = typer.Option(None, "--to", help="Agent you are switching to."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Build and show the payload without writing anything."
    ),
    no_llm: bool = typer.Option(
        False, "--no-llm", help="Skip LLMs and use the deterministic summary."
    ),
    print_only: bool = typer.Option(
        False, "--print", help="Print only the raw payload to stdout (stats go to stderr)."
    ),
) -> None:
    """Build a compressed handoff of the current session."""
    with error_boundary():
        if not dry_run:
            raise UserError(
                "Writing the handoff into an agent's files is not available yet.",
                hint="Preview it with `cb handoff --dry-run` (add --print for plain output).",
            )
        ws = open_workspace(ctx)
        config = ws.load_config()
        with ws.open_db() as db:
            session, _ = ws.active_session(db)
            context = build_context(
                db,
                session,
                config,
                project=ws.load_state().project_name,
                observer=git_observer_for(ws),
            )

        def notify(message: str) -> None:
            err_console.print(f"[yellow]cb:[/] {escape(message)}")

        handoff = build_handoff(
            context,
            config,
            from_agent=from_agent,
            to_agent=to_agent,
            no_llm=no_llm,
            notify=notify,
        )
        log.info(
            "handoff --dry-run: method=%s raw=%d compressed=%d",
            handoff.method,
            handoff.raw_tokens,
            handoff.compressed_tokens,
        )
        cap = config.handoff.hard_cap_tokens
        if print_only:
            typer.echo(handoff.payload, nl=False)
            err_console.print(_stats_table(handoff, cap))
            return
        console.print(
            Panel(
                escape(handoff.payload.rstrip()),
                title="Handoff payload (dry run, nothing written)",
                border_style="blue",
            )
        )
        console.print(_stats_table(handoff, cap))
