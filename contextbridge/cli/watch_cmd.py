"""Explicit foreground change watching."""

from __future__ import annotations

import sqlite3
import threading

import typer

from contextbridge.cli.common import console, error_boundary, open_workspace
from contextbridge.core.watcher import ProjectWatcher
from contextbridge.errors import UserError


def watch_command(
    ctx: typer.Context,
    interval: float = typer.Option(
        1.0, "--interval", min=0.05, help="Seconds between metadata scans."
    ),
    debounce: float = typer.Option(
        2.0, "--debounce", min=0.1, help="Quiet seconds before saving a batch."
    ),
    batch_window: float = typer.Option(
        30.0, "--batch-window", min=0.1, help="Maximum batch age in seconds."
    ),
    agent: str | None = typer.Option(None, "--agent", help="Tool in use, if known."),
    note: str = typer.Option("", "--note", help="Optional note attached to each batch."),
    seconds: float | None = typer.Option(
        None, "--seconds", min=0.1, help="Stop after this many seconds."
    ),
) -> None:
    """Watch until Ctrl-C. Store paths and size summaries in debounced batches."""
    with error_boundary():
        watcher = ProjectWatcher(
            open_workspace(ctx),
            interval=interval,
            debounce=debounce,
            batch_window=batch_window,
            agent=agent,
            notes=note,
        )
        console.print("Watching this project. Press Ctrl-C to stop and save the pending batch.")
        stop = threading.Event()
        try:
            metrics = watcher.run(stop, on_batch=console.print, seconds=seconds)
        except KeyboardInterrupt:
            metrics = watcher.metrics
        except (OSError, sqlite3.Error) as exc:
            raise UserError(
                f"Watching stopped because local files could not be read or saved: {exc}"
            ) from exc
        console.print(
            f"Stopped. {metrics.events} observed file changes, "
            f"{metrics.batches} batches, {metrics.files} recorded paths."
        )
