"""Typer application and `cb` console-script entry point."""

from __future__ import annotations

from pathlib import Path

import typer

from contextbridge import __version__
from contextbridge.cli.common import AppContext, console
from contextbridge.cli.handoff_cmd import handoff_command
from contextbridge.cli.hook_cmd import hook_app
from contextbridge.cli.init_cmd import init_command
from contextbridge.cli.log_cmd import log_command
from contextbridge.cli.run_cmd import run_cmd_command
from contextbridge.cli.search_cmd import search_command
from contextbridge.cli.status_cmd import status_command

app = typer.Typer(
    name="cb",
    help="ContextBridge: preserve project context across AI coding agents.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"cb {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    repo: Path | None = typer.Option(
        None,
        "--repo",
        help="Project directory to operate on (default: current directory).",
        file_okay=False,
        exists=True,
        resolve_path=True,
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging to cb.log."),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    ctx.obj = AppContext(repo=repo, verbose=verbose)


app.command("init")(init_command)
app.command("log")(log_command)
app.command("status")(status_command)
app.command("run", context_settings={"ignore_unknown_options": True})(run_cmd_command)
app.command("handoff")(handoff_command)
app.command("search")(search_command)
app.add_typer(hook_app, name="hook")


def run() -> None:
    app()


if __name__ == "__main__":
    run()
