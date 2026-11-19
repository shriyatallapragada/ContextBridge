from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from contextbridge.local_api import create_app
from contextbridge.providers.settings import ProviderSettingsService, ProviderStatus
from contextbridge.service import ProjectService
from contextbridge.storage.db import parse_utc


def client_for(root: Path) -> TestClient:
    app = create_app(root)
    return TestClient(app, headers={"Authorization": f"Bearer {app.state.session_token}"})


def finished(client: TestClient, path: str) -> dict:
    deadline = monotonic() + 3
    while monotonic() < deadline:
        data = client.get(path).json()
        if not data["running"]:
            return data
        sleep(0.02)
    raise AssertionError(f"Background operation did not finish: {path}")


def test_goal_handoff_notes_and_chat(git_repo: Path) -> None:
    (git_repo / "README.md").write_text("# Example\n\nReadme project description.\n")
    (git_repo / "chat.md").write_text("User: Please update the dashboard.")
    with client_for(git_repo) as client:

        def draft():
            return client.post("/v1/handoffs/preview", json={"to_agent": "generic"})

        assert "Readme project description" in draft().json()["payload"]
        note = client.post("/v1/notes", json={"text": "Goal: Earlier objective"})
        assert note.status_code == 201
        assert "Earlier objective" in draft().json()["payload"]
        assert client.get("/v1/dashboard").json()["project_goal"] == "Earlier objective"
        assert client.put("/v1/intent", json={"north_star": "Canonical objective"}).is_success
        assert client.get("/v1/notes").json()["counts"]["note"] == 1
        assert client.post("/v1/chat/import-file", json={"path": "chat.md"}).is_success
        assert not client.post("/v1/chat/import-file", json={"path": "../chat.md"}).is_success
        chat = client.post(
            "/v1/chat/handoff",
            json={"text": "User: Please update the dashboard.", "goal": "Update the dashboard"},
        ).json()
        assert chat["sections"]["Goal"] == ["Update the dashboard."]
    with client_for(git_repo) as client:
        body = {"to_agent": "generic", "from_agent": "generic"}
        preview = client.post("/v1/handoffs/preview", json=body).json()
        assert "Canonical objective" in preview["payload"]
        assert "same agent" in preview["warnings"][0]
        assert not (git_repo / "HANDOFF.md").exists()
        written = client.post("/v1/handoffs", json={**body, "preview_id": preview["preview_id"]})
        assert written.is_success and written.json()["payload"] == preview["payload"]
        assert "Canonical objective" in (git_repo / "HANDOFF.md").read_text()


def test_project_switch_preserves_data_and_clears_preview(git_repo: Path, tmp_path: Path):
    other = tmp_path / "other-project"
    other.mkdir()
    with client_for(git_repo) as client:
        client.put("/v1/intent", json={"north_star": "Original goal"})
        preview = client.post("/v1/handoffs/preview", json={"to_agent": "generic"}).json()
        switched = client.post("/v1/project", json={"path": str(other)})
        assert switched.status_code == 200
        assert Path(switched.json()["root"]) == other.resolve()
        assert client.get("/v1/dashboard").json()["project"] == "other-project"
        assert client.get("/v1/intent").json()["north_star"] is None
        assert (
            client.post(
                "/v1/handoffs", json={"to_agent": "generic", "preview_id": preview["preview_id"]}
            ).status_code
            == 400
        )
        assert not (other / "HANDOFF.md").exists()
        assert client.post("/v1/project", json={"path": str(git_repo)}).status_code == 200
        assert client.get("/v1/intent").json()["north_star"] == "Original goal"
        assert client.post("/v1/project", json={"path": str(other / "missing")}).status_code == 400
        assert client.get("/v1/intent").json()["north_star"] == "Original goal"
        assert (
            client.post(
                "/v1/project", json={"path": str(other)}, headers={"Authorization": ""}
            ).status_code
            == 401
        )


def test_project_switch_waits_for_background_work(git_repo: Path, monkeypatch):
    started, release = Event(), Event()

    def run(self, command, on_output=None):
        started.set()
        release.wait(3)
        raise RuntimeError("Fixture run finished")

    monkeypatch.setattr(ProjectService, "run_tests", run)
    with client_for(git_repo) as client:
        try:
            assert client.post("/v1/tests/run", json={"command": "fixture"}).status_code == 202
            assert started.wait(2)
            assert client.post("/v1/project", json={"path": str(git_repo)}).status_code == 409
        finally:
            release.set()
        finished(client, "/v1/tests/status")


def test_changes_command_and_settings(git_repo: Path, monkeypatch) -> None:
    (git_repo / "working.py").write_text("pass\n")
    monkeypatch.setattr(
        ProviderSettingsService,
        "check",
        lambda self, provider, model, endpoint, key: ProviderStatus(
            model == "exact-model" and key == "private-test-value", "Probe finished"
        ),
    )
    with client_for(git_repo) as client:
        changes = client.get("/v1/changes").json()
        assert "working.py" in {file["path"] for file in changes["files"]}
        assert (
            client.post("/v1/tests/run", json={"command": "echo dashboard-output"}).status_code
            == 202
        )
        run = finished(client, "/v1/tests/status")
        assert "dashboard-output" in run["output"]
        assert run["report"]["exit_code"] == 0
        assert client.get("/v1/tests/history").json()["runs"]
        settings = client.post("/v1/settings", json={"mode": "off", "provider": "none"})
        assert settings.is_success and "api_key" not in settings.json()
        probe = client.post(
            "/v1/settings/test",
            json={"provider": "ollama", "model": "exact-model", "api_key": "private-test-value"},
        )
        assert probe.json()["ok"] and "private-test-value" not in probe.text


@pytest.mark.parametrize(
    ("command", "exit_code", "counts", "recognized"),
    [
        ("exit 1", 1, (None, None, None), False),
        ("echo '=== 12 passed, 1 skipped in 0.01s ==='", 0, (12, 0, 1), True),
    ],
)
def test_command_report_and_history_contract(
    git_repo: Path, command: str, exit_code: int, counts: tuple, recognized: bool
) -> None:
    with client_for(git_repo) as client:
        assert client.post("/v1/tests/run", json={"command": command}).status_code == 202
        report = finished(client, "/v1/tests/status")["report"]
        assert report["exit_code"] == exit_code
        assert report["recognized"] is recognized
        assert tuple(report[key] for key in ("passed", "failed", "skipped")) == counts
        run = client.get("/v1/tests/history").json()["runs"][0]
        assert run["command"] == command
        assert run["exit_code"] == exit_code
        assert tuple(run[key] for key in ("passed", "failed", "skipped")) == counts
        assert parse_utc(run["ran_at"]).tzinfo is not None


def test_explanation_confirmation_progress_and_resume(git_repo: Path, monkeypatch) -> None:
    started, release = Event(), Event()
    calls = []

    def explain(self, *, max_requests, token_budget, component, notify):
        calls.append((component, max_requests, token_budget))
        notify("Cached explanation reused: app.py")
        started.set()
        if len(calls) == 1:
            release.wait(3)
            return "Stopped: 1/2 requests; 900/2000 reserved tokens. Retry in 2s."
        return "Finished: 0/2 requests; 0/2000 reserved tokens."

    monkeypatch.setattr(ProjectService, "project_explanation_notice", lambda *_: "Privacy notice")
    monkeypatch.setattr(ProjectService, "explain_project", explain)
    body = {"scope": "component", "component": "app", "max_requests": 2, "token_budget": 2000}
    with client_for(git_repo) as client:
        assert client.get("/v1/graph").is_success and not calls
        assert client.get("/v1/explain/notice?scope=component").json()["text"] == "Privacy notice"
        assert client.post("/v1/explain", json=body).status_code == 400
        body["confirm"] = True
        try:
            assert client.post("/v1/explain", json=body).status_code == 202
            assert started.wait(2)
            assert (
                client.post("/v1/explain", json={**body, "component": "elsewhere"}).status_code
                == 409
            )
            assert client.get("/v1/explain/status").json()["cache_hits"] == 1
        finally:
            release.set()
        stopped = finished(client, "/v1/explain/status")
        assert stopped["retry_in_seconds"] == 2 and stopped["tokens_reserved"] == 900
        assert client.post("/v1/explain", json=body).status_code == 202
        assert finished(client, "/v1/explain/status")["summary"].startswith("Finished")
        assert calls == [("app", 2, 2000)] * 2
