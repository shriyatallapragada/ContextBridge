"""MCP server tools: the same deterministic state, exposed to coding agents."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.helpers import commit_all, write

pytest.importorskip("mcp.server.fastmcp", reason="install the mcp extra to test the server")

from contextbridge.mcp_server import build_server

EXPECTED_TOOLS = {
    "get_project_status",
    "log_decision_or_todo",
    "set_project_intent",
    "list_tasks",
    "get_git_history",
    "get_architecture_graph",
}


def call(server: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke one tool in-process and decode its JSON text result."""
    content, _ = asyncio.run(server.call_tool(name, arguments))
    return json.loads(content[0].text)


@pytest.fixture()
def server() -> Any:
    return build_server()


def test_server_exposes_the_documented_tools(server: Any) -> None:
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description and len(tool.description) > 20
    assert "North Star" in server.instructions


def test_project_status_reports_intent_tasks_and_momentum(server: Any, git_repo: Path) -> None:
    write(git_repo, "app.py", "print('hi')\n")
    commit_all(git_repo, "add app")

    status = call(server, "get_project_status", {"project_dir": str(git_repo)})

    assert status["project"] == "project"
    assert status["branch"] == "main"
    assert status["north_star"] is None
    assert status["tasks"]["total"] == 0
    assert [c["subject"] for c in status["commits"]] == ["add app", "initial"]
    assert status["momentum"]["commits"] == 2
    assert status["open_tasks"] == []


def test_intent_and_todo_round_trip_into_sqlite(server: Any, git_repo: Path) -> None:
    call(
        server,
        "set_project_intent",
        {"north_star": "Keep the API deterministic", "project_dir": str(git_repo)},
    )
    logged = call(
        server,
        "log_decision_or_todo",
        {"content": "Ship the MCP bridge", "kind": "todo", "project_dir": str(git_repo)},
    )
    decision = call(
        server,
        "log_decision_or_todo",
        {"content": "Store intent in SQLite", "kind": "decision", "project_dir": str(git_repo)},
    )

    assert logged["source"] == "mcp" and logged["status"] == "open"
    assert decision["tag"] == "note" and decision["source"] == "mcp"

    status = call(server, "get_project_status", {"project_dir": str(git_repo)})
    assert status["north_star"] == "Keep the API deterministic"
    assert [t["content"] for t in status["open_tasks"]] == ["Ship the MCP bridge"]
    assert [n["content"] for n in status["recent_notes"]] == [
        "Store intent in SQLite",
        "Ship the MCP bridge",
    ]

    tasks = call(server, "list_tasks", {"project_dir": str(git_repo)})
    assert [t["content"] for t in tasks["tasks"]] == ["Ship the MCP bridge"]
    assert tasks["counts"]["open"] == 1


def test_graph_and_history_tools_are_deterministic(server: Any, git_repo: Path) -> None:
    (git_repo / "main.py").write_text("import json\n\ndef run():\n    return json\n")
    commit_all(git_repo, "add main")

    first = call(server, "get_architecture_graph", {"project_dir": str(git_repo)})
    second = call(server, "get_architecture_graph", {"project_dir": str(git_repo)})
    assert first == second
    assert first["view"] == "overview"
    assert all(node["id"].startswith("n_") for node in first["nodes"])

    history = call(server, "get_git_history", {"project_dir": str(git_repo), "limit": 1})
    assert len(history["commits"]) == 1
    assert history["commits"][0]["subject"] == "add main"


def test_tools_write_only_their_own_rows(server: Any, git_repo: Path) -> None:
    project_dir = str(git_repo)
    call(server, "set_project_intent", {"north_star": "focus", "project_dir": project_dir})
    call(server, "log_decision_or_todo", {"content": "do it", "project_dir": project_dir})

    from contextbridge.service import ProjectService

    with ProjectService(git_repo).ws.open_db() as db:
        assert db.get_intent() is not None
        rows = db.conn.execute(
            "SELECT tag, source FROM decision_logs WHERE content='do it'"
        ).fetchall()
    assert [(r["tag"], r["source"]) for r in rows] == [("todo", "mcp")]


def test_missing_mcp_extra_raises_a_guided_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    import contextbridge.mcp_server as module
    from contextbridge.errors import UserError

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("mcp."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(UserError, match="mcp extra"):
        module.build_server()
