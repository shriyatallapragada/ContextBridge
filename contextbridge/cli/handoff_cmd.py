"""`cb handoff`: compress the session context and inject it into the target agent's files.

``--dry-run`` shows everything without writing. A real run writes the adapter's file,
records the handoff in the ``handoffs`` table and updates ``last_handoff`` in state.json.
"""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from contextbridge.adapters.base_adapter import MAX_ACTIVE_FILES, BaseAdapter
from contextbridge.adapters.registry import get_adapter, resolve_name
from contextbridge.cli.common import (
    console,
    err_console,
    error_boundary,
    git_observer_for,
    log,
    open_workspace,
)
from contextbridge.core.sanitizer import sanitize
from contextbridge.errors import UnknownAgentError, UserError
from contextbridge.storage.db import utc_now
from contextbridge.synthesis.compressor import DETERMINISTIC, Handoff, build_handoff, format_ratio
from contextbridge.synthesis.context_builder import build_context
from contextbridge.synthesis.token_counter import APPROX_LABEL

MAX_LABEL_CHARS = 40


def _from_label(name: str | None) -> str | None:
    """The agent being left. Free text: it may be an agent we have no adapter for."""
    if not name or not name.strip():
        return None
    try:
        return resolve_name(name)
    except UnknownAgentError:
        return sanitize(name.strip().lower())[:MAX_LABEL_CHARS]


def _stats_table(handoff: Handoff, cap: int, target: str | None) -> Table:
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
    ratio = handoff.compression_ratio
    note = " [dim](payload is larger than the raw context: a very small session)[/]"
    table.add_row(
        "Compression", format_ratio(ratio) + (note if ratio is not None and ratio < 0 else "")
    )
    if target:
        table.add_row("Target", escape(target))
    return table


def handoff_command(
    ctx: typer.Context,
    from_agent: str | None = typer.Option(
        None, "--from", help="Agent you are leaving (any name; used as a label)."
    ),
    to_agent: str | None = typer.Option(
        None, "--to", help="Agent to hand off to: claude, cursor, generic (or an alias)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Build and show the payload without writing anything."
    ),
    no_llm: bool = typer.Option(
        False, "--no-llm", help="Skip LLMs and use the deterministic summary."
    ),
    print_only: bool = typer.Option(
        False, "--print", help="Print the raw payload to stdout (messages go to stderr)."
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite a HANDOFF.md / handoff.mdc not created by ContextBridge."
    ),
) -> None:
    """Hand the current session off to another agent."""
    with error_boundary():
        if not dry_run and (not from_agent or not to_agent):
            raise UserError(
                "`cb handoff` needs both --from and --to.",
                hint="Example: cb handoff --from claude --to cursor  (add --dry-run to preview).",
            )
        ws = open_workspace(ctx)
        config = ws.load_config()
        # Validate the target before doing any work, so a typo fails instantly.
        adapter: BaseAdapter | None = (
            get_adapter(to_agent, ws.root, force=force) if to_agent else None
        )
        from_label = _from_label(from_agent)

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
            from_agent=from_label,
            to_agent=adapter.name if adapter else None,
            no_llm=no_llm,
            notify=notify,
        )
        cap = config.handoff.hard_cap_tokens
        target = adapter.relative_path if adapter else None
        out = err_console if print_only else console

        if dry_run:
            log.info(
                "handoff --dry-run: method=%s raw=%d compressed=%d",
                handoff.method,
                handoff.raw_tokens,
                handoff.compressed_tokens,
            )
            if print_only:
                typer.echo(handoff.payload, nl=False)
            else:
                console.print(
                    Panel(
                        escape(handoff.payload.rstrip()),
                        title="Handoff payload (dry run, nothing written)",
                        border_style="blue",
                    )
                )
            out.print(_stats_table(handoff, cap, f"{target} (not written)" if target else None))
            return

        assert adapter is not None and from_label is not None
        active_files = [f.path for f in context.files[:MAX_ACTIVE_FILES]]
        written = adapter.inject_payload(handoff.payload, active_files)
        ratio = format_ratio(handoff.compression_ratio)
        with ws.open_db() as db:
            record = db.add_handoff(
                session.id,
                from_label,
                adapter.name,
                raw_tokens=handoff.raw_tokens,
                compressed_tokens=handoff.compressed_tokens,
                method=handoff.method,
                payload=handoff.payload,
                written_path=adapter.relative_path,
            )
            state = ws.load_state()
            state.last_handoff = {
                "timestamp": utc_now(),
                "from": from_label,
                "to": adapter.name,
                "raw_tokens": handoff.raw_tokens,
                "compressed_tokens": handoff.compressed_tokens,
                "compression_ratio": ratio,
                "method": handoff.method,
            }
            ws.save_state(state)
        log.info(
            "handoff: %s %s -> %s wrote %s method=%s raw=%d compressed=%d",
            record.id,
            from_label,
            adapter.name,
            adapter.relative_path,
            handoff.method,
            handoff.raw_tokens,
            handoff.compressed_tokens,
        )
        if print_only:
            typer.echo(handoff.payload, nl=False)
        out.print(
            f"[bold green]✓[/] Handed off [cyan]{escape(from_label)}[/] → "
            f"[cyan]{adapter.name}[/]: wrote [bold]{escape(str(written.relative_to(ws.root)))}[/]"
        )
        out.print(_stats_table(handoff, cap, None))
