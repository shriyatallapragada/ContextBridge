"""MCP server: expose ContextBridge state to coding agents over stdio.

Run it with ``cb mcp`` (or ``python -m contextbridge.mcp_server``) and add it to your
agent's MCP configuration. Every tool is deterministic SQLite/subprocess work — the
server never calls a model, so querying it costs 0 tokens beyond the tool payload.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from contextbridge import dashboard
from contextbridge.errors import UserError

SERVER_NAME = "contextbridge"
INSTRUCTIONS = (
    "ContextBridge is the local memory for this project: its North Star intent, "
    "its to-do list, its git momentum and its AST import graph. Read status before "
    "planning work, and log decisions or to-dos as you make them."
)
MAX_GRAPH_NODES = 60


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _service(project_dir: str) -> Any:
    if project_dir.strip():
        root = Path(project_dir).expanduser().resolve()
    else:
        configured = os.environ.get("CB_PROJECT_DIR", "")
        root = Path(configured).expanduser().resolve() if configured else Path.cwd()
    return dashboard.ensure_service(root)


def build_server() -> Any:
    """Construct the FastMCP server with every ContextBridge tool registered."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise UserError(
            "The MCP server needs the mcp extra.",
            hint="uv sync --extra mcp",
        ) from exc

    server = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)

    @server.tool()
    def get_project_status(project_dir: str = "") -> str:
        """North Star intent, open to-dos, recent notes and git momentum for a project.

        Call this first when asked what to work on or where the project stands.
        """
        return _json(dashboard.project_status(_service(project_dir)))

    @server.tool()
    def log_decision_or_todo(
        content: str,
        kind: Literal["todo", "decision", "note", "architecture", "blocker", "bug"] = "todo",
        project_dir: str = "",
    ) -> str:
        """Persist one decision or to-do in the project's SQLite store.

        Use kind='todo' for work items and kind='decision' (or 'architecture') for
        choices the next agent must not undo. Returns the stored record.
        """
        tag = "note" if kind == "decision" else kind
        record = dashboard.add_task(_service(project_dir), content, source="mcp", tag=tag)
        return _json(record)

    @server.tool()
    def set_project_intent(north_star: str, project_dir: str = "") -> str:
        """Set the persistent North Star project intent (one per project)."""
        return _json(dashboard.set_intent(_service(project_dir), north_star))

    @server.tool()
    def list_tasks(
        status: Literal["open", "resolved", "dismissed", "superseded"] = "open",
        project_dir: str = "",
    ) -> str:
        """List the project's to-do checklist, newest first, with per-status counts."""
        return _json(dashboard.list_tasks(_service(project_dir), status=status))

    @server.tool()
    def get_git_history(limit: int = 15, project_dir: str = "") -> str:
        """The last `limit` commits (default 15) with per-file diff stats and momentum."""
        bounded = max(1, min(int(limit), 100))
        return _json(dashboard.get_commits(_service(project_dir), limit=bounded))

    @server.tool()
    def get_architecture_graph(
        view: Literal["overview", "module", "flow"] = "overview", project_dir: str = ""
    ) -> str:
        """Deterministic AST import graph (nodes, edges, meta) for the Architecture tab.

        Read-only and token-free: relationships come from static analysis, never a model.
        """
        service = _service(project_dir)
        return _json(service.project_graph(view, limit=MAX_GRAPH_NODES))

    return server


def main() -> None:
    """Serve over stdio (the default MCP transport)."""
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
