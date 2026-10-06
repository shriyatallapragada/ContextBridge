"""`cb run -- <cmd>`: run a command, stream its output, and record test results."""

from __future__ import annotations

import shlex

import typer
from rich.markup import escape

from contextbridge.cli.common import app_context, err_console, log
from contextbridge.core.shell_listener import parse_output, run_command, unknown_output_failure
from contextbridge.errors import ContextBridgeError
from contextbridge.storage.workspace import Workspace


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
        ws = Workspace.locate(actx.start_dir)
        ws.setup_logging(verbose=actx.verbose)
        failures = report.failures if report.recognized else unknown_output_failure(result)
        with ws.open_db() as db:
            session, _ = ws.active_session(db)
            run = db.add_test_run(
                session.id,
                shlex.join(command),
                result.exit_code,
                passed=report.passed,
                failed=report.failed,
                skipped=report.skipped,
                failures=failures,
            )
        log.info(
            "run: %s exit=%d runner=%s passed=%s failed=%s",
            run.id,
            result.exit_code,
            report.runner,
            report.passed,
            report.failed,
        )
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
