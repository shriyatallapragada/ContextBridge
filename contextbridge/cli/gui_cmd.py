"""`cb gui`: open the desktop app."""

from __future__ import annotations

import typer

from contextbridge.cli.common import error_boundary
from contextbridge.errors import EnvironmentProblemError


def gui_command() -> None:
    """Open the ContextBridge desktop app."""
    with error_boundary():
        try:
            from contextbridge.gui.app import run_gui
        except ImportError as exc:
            raise EnvironmentProblemError(
                "The desktop app needs an extra package that isn't installed.",
                hint="Install it with:  pip install 'contextbridge[gui]'",
            ) from exc
        raise typer.Exit(run_gui())
