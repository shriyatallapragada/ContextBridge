"""Real generation probes and actionable, safe missing-model diagnostics."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from contextbridge.main import app
from contextbridge.providers import credentials
from contextbridge.providers.http import HTTPProvider, ModelNotFound
from contextbridge.providers.settings import ProviderSettingsService
from contextbridge.service import ProjectService

MODEL = "llama-3.3-70b-versatile"
ENDPOINT = "https://api.groq.com/openai/v1"


@pytest.fixture
def mock_http(monkeypatch):
    original = httpx.Client

    def install(handler):
        monkeypatch.setattr(
            httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
        )

    return install


def test_check_generates_with_exact_unsaved_model_and_key(tmp_path, mock_http, monkeypatch):
    calls = []
    monkeypatch.setattr(credentials, "get_key", lambda endpoint: None)

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    mock_http(respond)
    service = ProviderSettingsService(tmp_path)
    status = service.check("openai-compatible", MODEL, ENDPOINT, "unsaved-private-key")
    assert status.ok and MODEL in status.message and ENDPOINT in status.message
    assert len(calls) == 1
    assert calls[0].method == "POST" and str(calls[0].url) == ENDPOINT + "/chat/completions"
    body = json.loads(calls[0].content)
    assert body["model"] == MODEL and body["max_tokens"] == 64
    assert body["messages"] == [{"role": "user", "content": "Reply with OK."}]
    assert calls[0].headers["Authorization"] == "Bearer unsaved-private-key"
    assert not service.path.exists()  # A check never saves a choice or key.
    assert "unsaved-private-key" not in status.message


@pytest.mark.parametrize(
    "status,code",
    [(404, "model_not_found"), (400, "model_not_found"), (410, "model_decommissioned")],
)
def test_missing_model_reports_selection_endpoint_and_offered_models(mock_http, status, code):
    calls = []

    def respond(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(status, json={"error": {"code": code, "message": "private-key"}})
        return httpx.Response(200, json={"data": [{"id": MODEL}, {"id": "other-model"}]})

    mock_http(respond)
    with pytest.raises(ModelNotFound) as error:
        HTTPProvider("misspelled-model", ENDPOINT, api_key="private-key").generate("Hello")
    message = str(error.value)
    assert "misspelled-model" in message and ENDPOINT + "/chat/completions" in message
    assert MODEL in message and "other-model" in message
    assert "private-key" not in message
    assert [r.method for r in calls] == ["POST", "GET"]
    assert str(calls[-1].url) == ENDPOINT + "/models"


def test_bad_url_check_does_not_succeed_just_because_models_listing_works(tmp_path, mock_http):
    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        return httpx.Response(404)

    mock_http(respond)
    status = ProviderSettingsService(tmp_path).check(
        "openai-compatible", MODEL, ENDPOINT + "/wrong-path", "key"
    )
    assert not status.ok and ENDPOINT + "/wrong-path/chat/completions" in status.message
    assert "Offered models:" in status.message


def test_missing_model_and_listing_failure_do_not_recurse_or_expose_keys(mock_http):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(404, text="server echoed private-key")

    mock_http(respond)
    with pytest.raises(ModelNotFound) as error:
        HTTPProvider(MODEL, ENDPOINT, api_key="private-key").generate("Hello")
    assert len(calls) == 2
    assert "could not be retrieved" in str(error.value)
    assert MODEL in str(error.value) and ENDPOINT in str(error.value)
    assert "private-key" not in str(error.value)


def test_untrusted_offered_model_names_do_not_echo_the_api_key(mock_http):
    def respond(request):
        return (
            httpx.Response(404)
            if request.method == "POST"
            else httpx.Response(200, json={"data": [{"id": "private-key"}]})
        )

    mock_http(respond)
    with pytest.raises(ModelNotFound) as error:
        HTTPProvider(MODEL, ENDPOINT, api_key="private-key").generate("Hello")
    assert "private-key" not in str(error.value)


def test_ollama_check_also_generates_with_selected_model(tmp_path, mock_http):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"message": {"content": "OK"}})

    mock_http(respond)
    status = ProviderSettingsService(tmp_path).check(
        "ollama", "local-model", "http://localhost:11434"
    )
    assert status.ok and len(calls) == 1
    assert calls[0].url.path == "/api/chat"
    body = json.loads(calls[0].content)
    assert body["model"] == "local-model" and body["options"]["num_predict"] == 64


def test_saved_model_reaches_cli_and_missing_model_details_reach_output(
    git_repo, monkeypatch, mock_http
):
    project = ProjectService(git_repo)
    project.init(install_hook=False)
    monkeypatch.setattr(credentials, "get_key", lambda endpoint: "private-key")
    settings = ProviderSettingsService(project.ws.context_dir)
    settings.save("openai-compatible", MODEL, ENDPOINT)
    assert settings.load().llm.model == MODEL
    calls = []

    def respond(request):
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["model"] == MODEL
            return httpx.Response(404)
        return httpx.Response(200, json={"data": [{"id": "offered-model"}]})

    mock_http(respond)
    result = CliRunner().invoke(app, ["handoff", "--to", "codex", "--dry-run", "--polish"])
    assert result.exit_code == 0, result.output
    assert MODEL in result.output and "offered-model" in result.output
    assert "api.groq.com" in result.output and "private-key" not in result.output
    assert len(calls) == 2
