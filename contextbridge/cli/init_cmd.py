"""`cb init`: create `.context/`, the database, state and config; start a session."""

from __future__ import annotations

import typer

from contextbridge.cli.common import app_context, console, error_boundary, log
from contextbridge.storage.workspace import Workspace, ensure_gitignored, find_project_root


def init_command(
    ctx: typer.Context,
    no_hooks: bool = typer.Option(
        False, "--no-hooks", help="Do not install the git post-commit hook."
    ),
) -> None:
    """Initialize ContextBridge in the current project. Safe to run repeatedly."""
    with error_boundary():
        actx = app_context(ctx)
        root = find_project_root(actx.start_dir)
        ws, created_dir = Workspace.create(root)
        ws.setup_logging(verbose=actx.verbose)
        ws.load_config()  # Fail early on an invalid config file.

        gitignore_changed = ensure_gitignored(root)
        # Migrate explicitly (not on open) so we can report what was applied.
        with ws.open_db(migrate=False) as db:
            migrations = db.migrate()
            schema_version = db.schema_version
            state = ws.load_state()
            session, created_session = ws.resolve_active_session(db, state)
            ws.save_state(state)

        log.info("init: root=%s session=%s", root, session.id)
        verb = "Initialized" if created_dir else "Already initialized"
        console.print(f"[bold green]✓[/] {verb} ContextBridge in [cyan]{ws.context_dir}[/]")
        if gitignore_changed:
            console.print("  • Added [cyan].context/[/] to .gitignore")
        if migrations:
            console.print(f"  • Database schema at v{schema_version}")
        session_verb = "Started" if created_session else "Resumed"
        console.print(
            f"  • {session_verb} session [magenta]{session.id}[/] "
            f"on branch [cyan]{session.branch_name}[/]"
        )
        if not no_hooks:
            # Hook installation is part of the git observer (Phase 2).
            console.print("  • [dim]Git hooks: not installed yet (coming soon)[/]")
