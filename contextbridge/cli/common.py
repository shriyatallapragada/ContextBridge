"""Shared CLI plumbing: global options, console, and the error boundary."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console

from contextbridge.errors import ContextBridgeError
from contextbridge.storage.workspace import Workspace

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)
log = logging.getLogger("contextbridge")


@dataclass
class AppContext:
    """Global options shared by every command (stored on ``ctx.obj``)."""

    repo: Path | None = None
    verbose: bool = False

    @property
    def start_dir(self) -> Path:
        return (self.repo or Path.cwd()).resolve()


def app_context(ctx: typer.Context) -> AppContext:
    obj = ctx.find_object(AppContext)
    return obj if obj is not None else AppContext()


def open_workspace(ctx: typer.Context) -> Workspace:
    """Locate `.context/` from the start dir and set up file logging."""
    actx = app_context(ctx)
    ws = Workspace.locate(actx.start_dir)
    ws.setup_logging(verbose=actx.verbose)
    return ws


@contextmanager
def error_boundary() -> Iterator[None]:
    """Turn expected errors into a clean message plus the spec'd exit code."""
    try:
        yield
    except ContextBridgeError as exc:
        err_console.print(f"[bold red]Error:[/] {exc.message}")
        if exc.hint:
            err_console.print(f"[dim]{exc.hint}[/]")
        log.error("%s: %s", type(exc).__name__, exc.message)
        raise typer.Exit(exc.exit_code) from exc
