"""MCP server startup; the `mcp` extra stays optional until this command is used."""

from __future__ import annotations

import os

import typer

from contextbridge.cli.common import app_context, error_boundary


def mcp_command(ctx: typer.Context) -> None:
    """Serve ContextBridge tools to MCP clients over stdio (get_project_status, ...)."""
    with error_boundary():
        start = app_context(ctx).start_dir
        # MCP clients spawn servers with their own cwd; pin the project explicitly.
        os.environ.setdefault("CB_PROJECT_DIR", str(start))
        os.chdir(start)
        from contextbridge.mcp_server import main as serve_stdio

        serve_stdio()
