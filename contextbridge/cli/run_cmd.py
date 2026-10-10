"""`cb run -- <cmd>`: run a command, stream its output, and record test results."""

from __future__ import annotations

import shlex

import typer
from rich.markup import escape

from contextbridge.cli.common import app_context, err_console, log
from contextbridge.core.shell_listener import parse_output, run_command
from contextbridge.errors import ContextBridgeError
from contextbridge.service import ProjectService


def run_cmd_command(
    ctx: typer.Context,
    command: list[str] = typer.Argument(
        ..., help="The command to run, after `--` (e.g. `cb run -- pytest -q`).", metavar="CMD..."
    ),
) -> None:
    """Run a command and record its test results. Exits with the command's exit code.

    Tip: alias it in your shell, e.g. `alias t='cb run -- pytest'`.
    """
    actx = app_context(ctx)
    result = run_command(command, cwd=actx.start_dir)
    report = parse_output(command, result.output)
    try:
        service = ProjectService.for_path(actx.start_dir)
        service.record_test_run(shlex.join(command), result, report)
        if report.recognized:
            summary = f"{report.passed if report.passed is not None else '?'} passed, " + (
                f"{report.failed} failed, {report.skipped} skipped"
            )
            err_console.print(f"[dim]cb: recorded {report.runner} run ({escape(summary)})[/]")
        else:
            err_console.print(f"[dim]cb: recorded exit code {result.exit_code}[/]")
    except ContextBridgeError as exc:
        err_console.print(f"[yellow]cb: result not recorded:[/] {escape(exc.message)}")
    except Exception as exc:  # Recording must never change the command's outcome.
        log.exception("run: failed to record result")
        err_console.print(f"[yellow]cb: result not recorded:[/] {escape(type(exc).__name__)}")
    raise typer.Exit(result.exit_code)
