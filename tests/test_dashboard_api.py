"""Dashboard API: North Star intent, SQLite tasks, git momentum, Chat Bridge import."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contextbridge.local_api import create_app
from contextbridge.providers import credentials
from contextbridge.providers.http import HTTPProvider
from contextbridge.service import ProjectService
from contextbridge.storage.db import Database
from tests.helpers import commit_all, write

TRANSCRIPT = """\
Next steps:
- [ ] parse the transcript deterministically
- [ ] wire the React tabs

TODO: publish the MCP bridge
"""

DASH_PATHS = [
    ("GET", "/v1/dashboard"),
    ("GET", "/v1/intent"),
    ("PUT", "/v1/intent"),
    ("GET", "/v1/tasks"),
    ("POST", "/v1/tasks"),
    ("PATCH", "/v1/tasks/dec_whatever"),
    ("DELETE", "/v1/tasks/dec_whatever"),
    ("GET", "/v1/commits"),
    ("POST", "/v1/chat-import"),
]


def bearer(api) -> str:
    return "Bearer " + api.state.session_token


@pytest.mark.parametrize("method,path", DASH_PATHS)
def test_dashboard_endpoints_require_the_session_token(tmp_path: Path, method, path) -> None:
    api = create_app(tmp_path)
    with TestClient(api) as client:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            response = client.request(method, path, json={}, headers=headers)
            assert response.status_code == 401
    assert not (tmp_path / ".contextbridge").exists()


def test_north_star_intent_persists_across_server_restarts(git_repo: Path) -> None:
    first = create_app(git_repo)
    with TestClient(first, headers={"Authorization": bearer(first)}) as client:
        assert client.get("/v1/intent").json() == {"north_star": None, "updated_at": None}
        saved = client.put("/v1/intent", json={"north_star": "Ship v0.2.0 without friction"})
        assert saved.status_code == 200
        assert saved.json()["north_star"] == "Ship v0.2.0 without friction"

    second = create_app(git_repo)
    with TestClient(second, headers={"Authorization": bearer(second)}) as client:
        payload = client.get("/v1/intent").json()
        assert payload["north_star"] == "Ship v0.2.0 without friction"
        assert payload["updated_at"]
        # Single row: replacing the intent updates it instead of accumulating.
        client.put("/v1/intent", json={"north_star": "Second star"})
        assert client.get("/v1/intent").json()["north_star"] == "Second star"
        assert client.put("/v1/intent", json={"north_star": "   "}).status_code == 400


def test_task_checklist_round_trips_through_sqlite(git_repo: Path) -> None:
    api = create_app(git_repo)
    with TestClient(api, headers={"Authorization": bearer(api)}) as client:
        created = client.post("/v1/tasks", json={"content": "Add the timeline view"})
        assert created.status_code == 201
        task = created.json()
        assert task["status"] == "open" and task["source"] == "manual"

        listed = client.get("/v1/tasks").json()
        assert [t["content"] for t in listed["tasks"]] == ["Add the timeline view"]
        assert listed["counts"]["open"] == 1

        toggled = client.patch(f"/v1/tasks/{task['id']}", json={"status": "resolved"})
        assert toggled.json()["status"] == "resolved"
        assert client.get("/v1/tasks?status=resolved").json()["counts"]["resolved"] == 1
        assert client.get("/v1/tasks?status=open").json()["tasks"] == []
        assert len(client.get("/v1/tasks").json()["tasks"]) == 1  # no filter means all

        assert client.delete(f"/v1/tasks/{task['id']}").json()["deleted"] is True
        assert client.get("/v1/tasks").json()["counts"]["total"] == 0

        assert client.delete("/v1/tasks/dec_missing").status_code == 400
        assert client.patch("/v1/tasks/dec_missing", json={"status": "open"}).status_code == 400
        assert client.patch(f"/v1/tasks/{task['id']}", json={"status": "done"}).status_code == 422
        assert client.post("/v1/tasks", json={"content": " "}).status_code == 400


def test_chat_bridge_import_parses_then_commits(git_repo: Path) -> None:
    api = create_app(git_repo)
    with TestClient(api, headers={"Authorization": bearer(api)}) as client:
        preview = client.post("/v1/chat-import", json={"text": TRANSCRIPT, "commit": False})
        assert preview.status_code == 200
        parsed = preview.json()["parsed"]
        assert [p["content"] for p in parsed] == [
            "parse the transcript deterministically",
            "wire the React tabs",
            "publish the MCP bridge",
        ]
        assert preview.json()["committed"] is False
        assert client.get("/v1/tasks").json()["tasks"] == []  # preview stores nothing

        imported = client.post("/v1/chat-import", json={"text": TRANSCRIPT, "commit": True})
        assert imported.status_code == 200
        stored = imported.json()["created"]
        assert len(stored) == 3
        assert {t["source"] for t in stored} == {"chat"}
        assert imported.json()["counts"]["open"] == 3

        again = client.post("/v1/chat-import", json={"text": TRANSCRIPT, "commit": True})
        assert again.json()["created"] == []
        assert again.json()["duplicates"] == 3
        assert client.get("/v1/tasks").json()["counts"]["total"] == 3

        chat_only = client.get("/v1/tasks?source=chat").json()
        assert len(chat_only["tasks"]) == 3
        assert client.get("/v1/tasks?source=mcp").json()["tasks"] == []


def test_boot_reads_git_history_before_any_request(git_repo: Path) -> None:
    project = ProjectService(git_repo)
    project.init(install_hook=False)
    for index in range(3):
        write(git_repo, f"mod{index}.py", f"value = {index}\n")
        commit_all(git_repo, f"commit {index}")

    api = create_app(git_repo)  # no request is made

    with Database.open(project.ws.db_path) as db:
        stored = db.list_git_commits()
    assert [c.subject for c in stored] == ["commit 2", "commit 1", "commit 0", "initial"]
    assert stored[0].files[0].path == "mod2.py"
    assert stored[0].insertions == 1

    with TestClient(api, headers={"Authorization": bearer(api)}) as client:
        payload = client.get("/v1/commits").json()
        assert [c["subject"] for c in payload["commits"]] == [
            "commit 2",
            "commit 1",
            "commit 0",
            "initial",
        ]
        assert payload["momentum"]["commits"] == 4
        refreshed = client.get("/v1/commits?refresh=true").json()
        assert [c["sha"] for c in refreshed["commits"]] == [c["sha"] for c in payload["commits"]]


def test_overview_bundle_feeds_the_first_tab(git_repo: Path) -> None:
    project = ProjectService(git_repo)
    project.init(install_hook=False)
    api = create_app(git_repo)
    with TestClient(api, headers={"Authorization": bearer(api)}) as client:
        client.put("/v1/intent", json={"north_star": "Ship the dashboard"})
        client.post("/v1/tasks", json={"content": "first task"})
        overview = client.get("/v1/dashboard").json()

    assert overview["project"] == "project"
    assert overview["branch"] == "main"
    assert overview["initialized"] is True
    assert overview["north_star"] == "Ship the dashboard"
    assert overview["tasks"]["open"] == 1
    assert overview["commits"][0]["subject"] == "initial"
    assert overview["momentum"]["commits"] == 1
    assert overview["generated_at"].endswith("Z")
    assert project.is_initialized()


def test_dashboard_endpoints_never_touch_models_or_credentials(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (git_repo / "main.py").write_text('"""Sample app."""\ndef run():\n    return 1\n')
    project = ProjectService(git_repo)
    project.init(install_hook=False)
    monkeypatch.setattr(HTTPProvider, "generate", lambda *_: pytest.fail("model called"))
    monkeypatch.setattr(credentials, "get_key", lambda *_: pytest.fail("credential read"))
    api = create_app(git_repo)
    with TestClient(api, headers={"Authorization": bearer(api)}) as client:
        for path in (
            "/v1/dashboard",
            "/v1/intent",
            "/v1/tasks",
            "/v1/commits",
            "/v1/graph?view=overview",
        ):
            assert client.get(path).status_code == 200, path
        assert (
            client.post(
                "/v1/chat-import", json={"text": "- [ ] offline task", "commit": True}
            ).status_code
            == 200
        )
        assert client.put("/v1/intent", json={"north_star": "offline"}).status_code == 200
        assert api.state.session_token not in client.get("/v1/dashboard").text


def test_ui_shell_serves_without_a_token_but_data_still_needs_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>ContextBridge</title>")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log('ui')")
    monkeypatch.setenv("CB_UI_DIR", str(dist))

    api = create_app(tmp_path)
    with TestClient(api) as client:
        shell = client.get("/ui")
        assert shell.status_code == 200 and "ContextBridge" in shell.text
        assert client.get("/ui/").status_code == 200
        assert client.get("/ui/assets/app.js").status_code == 200
        assert client.get("/v1/dashboard").status_code == 401  # data never rides the shell
