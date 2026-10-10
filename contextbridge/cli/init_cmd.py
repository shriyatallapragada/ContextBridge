"""`cb init`: create `.context/`, the database, state and config; start a session."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape

from contextbridge.cli.common import app_context, console, error_boundary, log
from contextbridge.core.git_observer import GitObserver, HookAction, HookResult
from contextbridge.errors import NotAGitRepoError
from contextbridge.service import ProjectService
from contextbridge.storage.workspace import find_project_root

_INSTALL_MESSAGES = {
    HookAction.created: "Installed git post-commit hook",
    HookAction.appended: "Added ContextBridge to your existing post-commit hook",
    HookAction.already_installed: "Git post-commit hook already installed",
}


def _report_install(result: HookResult) -> None:
    path = escape(str(result.path))
    if result.action == HookAction.disabled_hook:
        console.print(
            f"  • [yellow]![/] {path} exists but is not executable (disabled), so it was "
            "left untouched and ContextBridge will not record commits. Make it executable "
            "and re-run `cb init`, or remove it."
        )
        return
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
        result = ProjectService(root).init(install_hook=not no_hooks, verbose=actx.verbose)

        verb = "Initialized" if result.created_dir else "Already initialized"
        console.print(
            f"[bold green]✓[/] {verb} ContextBridge in [cyan]{result.workspace.context_dir}[/]"
        )
        if result.gitignore_changed:
            console.print("  • Added [cyan].context/[/] to .gitignore")
        if result.migrations:
            console.print(f"  • Database schema at v{result.schema_version}")
        session_verb = "Started" if result.created_session else "Resumed"
        console.print(
            f"  • {session_verb} session [magenta]{result.session.id}[/] "
            f"on branch [cyan]{result.session.branch_name}[/]"
        )
        if result.hook_skipped == "no-hooks":
            console.print("  • [dim]Git hooks: skipped (--no-hooks)[/]")
        elif result.hook_skipped == "not-a-git-repo":
            console.print("  • [dim]Git hooks: skipped (not a git repository)[/]")
        elif result.hook is not None:
            _report_install(result.hook)
