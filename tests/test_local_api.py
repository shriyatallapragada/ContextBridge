"""Session authentication, local-only binding and the shared engine endpoints."""

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from contextbridge.config import save_config
from contextbridge.errors import UserError
from contextbridge.local_api import create_app, run_server
from contextbridge.main import app as cli
from contextbridge.providers import credentials
from contextbridge.providers.http import HTTPProvider
from contextbridge.service import ProjectService


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/"),
        ("GET", "/v1/status"),
        ("GET", "/v1/graph"),
        ("GET", "/v1/summary"),
        ("GET", "/v1/mermaid"),
        ("GET", "/v1/openapi"),
        ("GET", "/docs"),
        ("OPTIONS", "/v1/graph"),
        ("POST", "/v1/handoffs/preview"),
        ("POST", "/v1/handoffs"),
    ],
)
def test_every_request_requires_the_session_token(tmp_path, method, path):
    api = create_app(tmp_path)
    with TestClient(api) as client:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            response = client.request(method, path, headers=headers)
            assert response.status_code == 401
        assert client.request(method, path + "?token=" + api.state.session_token).status_code == 401
    assert not (tmp_path / ".contextbridge").exists()


def test_random_tokens_are_per_session_and_old_tokens_do_not_work(tmp_path):
    first, second = create_app(tmp_path), create_app(tmp_path)
    assert first.state.session_token != second.state.session_token
    assert len(first.state.session_token) >= 40
    with TestClient(second) as client:
        assert (
            client.get(
                "/", headers={"Authorization": "Bearer " + first.state.session_token}
            ).status_code
            == 401
        )
        response = client.get(
            "/", headers={"Authorization": "Bearer " + second.state.session_token}
        )
        assert response.status_code == 200
        assert second.state.session_token not in response.text


def test_authenticated_endpoints_use_engine_without_model_or_key_reads(git_repo, monkeypatch):
    project = ProjectService(git_repo)
    project.init(install_hook=False)
    (git_repo / "main.py").write_text('"""Run the sample app."""\ndef run(): pass\n')
    save_config(
        {
            "llm": {
                "provider": "openai-compatible",
                "model": "m",
                "base_url": "https://example.test/v1",
            }
        },
        project.ws.context_dir / "config.json",
    )
    monkeypatch.setattr(HTTPProvider, "generate", lambda *_: pytest.fail("model called"))
    monkeypatch.setattr(credentials, "get_key", lambda *_: pytest.fail("credential read"))
    api = create_app(git_repo)
    with TestClient(api, headers={"Authorization": "Bearer " + api.state.session_token}) as client:
        for path in (
            "/v1/status",
            "/v1/summary",
            "/v1/openapi",
            "/v1/graph?view=overview",
            "/v1/graph?view=flow",
            "/v1/graph?view=module&module=main.py",
        ):
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert api.state.session_token not in response.text
        assert client.get("/v1/mermaid").text.startswith("flowchart TD")
        preview = client.post("/v1/handoffs/preview", json={"to_agent": "generic"})
        assert preview.status_code == 200, preview.text
        assert preview.json()["written"] is False
        assert not (git_repo / "HANDOFF.md").exists()
        written = client.post("/v1/handoffs", json={"to_agent": "generic"})
        assert written.status_code == 200, written.text
        assert (git_repo / "HANDOFF.md").read_text().find(written.json()["payload"].strip()) >= 0
        assert client.get("/v1/graph?limit=10000").status_code == 422
        assert client.get("/v1/graph?view=module&module=../../private.py").status_code == 400
        bad = client.post("/v1/handoffs", json={"api_key": "do-not-echo-me"})
        assert bad.status_code == 422 and "do-not-echo-me" not in bad.text


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "::", "192.168.1.5"])
def test_non_loopback_bind_is_rejected(tmp_path, host):
    with pytest.raises(UserError, match=r"127\.0\.0\.1"):
        run_server(tmp_path, host=host)


def test_open_browser_uses_authenticated_dashboard_url(tmp_path, monkeypatch):
    import threading
    import webbrowser

    import uvicorn

    opened = []
    apps = []

    class ImmediateTimer:
        def __init__(self, interval, callback, args):
            self.callback, self.args = callback, args

        def start(self):
            self.callback(*self.args)

    monkeypatch.setattr(threading, "Timer", ImmediateTimer)
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: apps.append(app))
    monkeypatch.setattr("contextbridge.local_api._ui_directory", lambda: tmp_path)
    run_server(tmp_path, port=9876, open_browser=True)
    assert opened == [f"http://127.0.0.1:9876/ui/#token={apps[0].state.session_token}"]


def test_serve_binds_only_loopback_and_prints_fresh_token(tmp_path, monkeypatch, capsys):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    run_server(tmp_path, port=9876)
    app, options = calls[0]
    assert options["host"] == "127.0.0.1" and options["port"] == 9876
    assert options["proxy_headers"] is False and options["access_log"] is False
    assert options["workers"] == 1
    output = capsys.readouterr().out
    assert output.count(app.state.session_token) == 1
    result = CliRunner().invoke(cli, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code == 2
    assert len(calls) == 1
