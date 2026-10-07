"""`cb init`: create `.context/`, the database, state and config; start a session."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape

from contextbridge.cli.common import app_context, console, error_boundary, log
from contextbridge.core.git_observer import GitObserver, HookAction, HookResult
from contextbridge.errors import NotAGitRepoError
from contextbridge.storage.workspace import Workspace, ensure_gitignored, find_project_root

_INSTALL_MESSAGES = {
    HookAction.created: "Installed git post-commit hook",
    HookAction.appended: "Added ContextBridge to your existing post-commit hook",
    HookAction.already_installed: "Git post-commit hook already installed",
}


def _report_install(result: HookResult) -> None:
    path = escape(str(result.path))
    if result.action == HookAction.unsupported_shell:
        console.print(
            f"  • [yellow]![/] {path} is not a shell script; hook not installed. Add this "
            "line to it manually: [cyan]cb hook post-commit >/dev/null 2>&1 &[/]"
        )
        return
    console.print(f"  • {_INSTALL_MESSAGES[result.action]} [dim]({path})[/]")


def _uninstall_hooks(root: Path) -> None:
    try:
        observer = GitObserver.open(root)
    except NotAGitRepoError:
        console.print("[dim]Not a git repository; no hooks to remove.[/]")
        return
    result = observer.uninstall_hook()
    path = escape(str(result.path))
    if result.action == HookAction.not_installed:
        console.print(f"[dim]No ContextBridge hook found in {path}.[/]")
    elif result.action == HookAction.removed_file:
        console.print(f"[bold green]✓[/] Removed hook file {path}")
    else:
        console.print(f"[bold green]✓[/] Removed ContextBridge block from {path}")
    log.info("init: uninstall hooks -> %s", result.action.value)


def init_command(
    ctx: typer.Context,
    no_hooks: bool = typer.Option(
        False, "--no-hooks", help="Do not install the git post-commit hook."
    ),
    uninstall_hooks: bool = typer.Option(
        False, "--uninstall-hooks", help="Remove the ContextBridge git hook block and exit."
    ),
) -> None:
    """Initialize ContextBridge in the current project. Safe to run repeatedly."""
    with error_boundary():
        actx = app_context(ctx)
        root = find_project_root(actx.start_dir)
        if uninstall_hooks:
            _uninstall_hooks(root)
            return
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
        if no_hooks:
            console.print("  • [dim]Git hooks: skipped (--no-hooks)[/]")
            return
        try:
            observer = GitObserver.open(root)
        except NotAGitRepoError:
            console.print("  • [dim]Git hooks: skipped (not a git repository)[/]")
            return
        result = observer.install_hook()
        log.info("init: hook %s at %s", result.action.value, result.path)
        _report_install(result)
