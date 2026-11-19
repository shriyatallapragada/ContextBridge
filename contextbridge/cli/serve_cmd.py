"""Explicit local server startup; API dependencies stay optional."""

from __future__ import annotations

import typer

from contextbridge.cli.common import app_context, error_boundary
from contextbridge.errors import UserError


def serve_command(
    ctx: typer.Context,
    port: int = typer.Option(8765, "--port", min=1, max=65535),
    open_browser: bool = typer.Option(False, "--open", help="Open the dashboard in your browser."),
) -> None:
    """Serve this project on 127.0.0.1, with a fresh token printed for this session."""
    with error_boundary():
        try:
            from contextbridge.local_api import run_server
        except ImportError:
            raise UserError(
                "Install the local API extra first.",
                hint="uv sync --extra api --extra gui --extra dev",
            ) from None
        run_server(app_context(ctx).start_dir, port=port, open_browser=open_browser)
