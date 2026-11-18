"""The provider module and its older compatibility imports work without model SDKs."""

import json

import httpx
import pytest

from contextbridge.config import Config
from contextbridge.providers import candidates, create_provider
from contextbridge.providers.base import ProviderError, ProviderTimeout, ProviderUnavailable
from contextbridge.providers.http import HTTPProvider
from contextbridge.synthesis.llm_client import CloudClient, OllamaClient


def test_default_and_none_make_no_requests():
    assert candidates(Config()) == []
    assert create_provider(Config()).generate("hello") == ""


def test_routing_preserves_explicit_legacy_choices(monkeypatch):
    cfg = Config()
    cfg.llm.provider = "ollama"
    assert [provider.provider for provider in candidates(cfg)] == ["ollama"]
    cfg.llm.cloud_fallback = True
    assert [provider.provider for provider in candidates(cfg)] == ["ollama", "groq"]
    cfg.llm.provider = "openai"
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider = create_provider(cfg)
    assert provider.model == "gpt-4o-mini"
    assert candidates(cfg, no_llm=True) == []


def test_ollama_explicit_context_size_and_structured_schema(monkeypatch):
    calls = []

    def request(self, path, body):
        calls.append((path, body))
        return {"message": {"content": '{"value": 7}'}}

    monkeypatch.setattr(HTTPProvider, "request", request)
    provider = OllamaClient("http://localhost:11434", "local-model", 2)
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
    assert json.loads(provider.generate("task", schema)) == {"value": 7}
    assert calls[0][1]["options"]["num_ctx"] == 16384
    assert calls[0][1]["format"] == schema
    assert not provider.is_cloud


def test_compatible_endpoint_and_schema(monkeypatch):
    calls = []

    def request(self, path, body):
        calls.append((path, body))
        return {"choices": [{"message": {"content": '{"value": 7}'}}]}

    monkeypatch.setattr(HTTPProvider, "request", request)
    provider = HTTPProvider("user-model", "https://example.test/v1", api_key="private-key")
    schema = {"type": "object"}
    provider.generate("task", schema)
    assert calls[0][0] == "/chat/completions"
    assert calls[0][1]["response_format"]["json_schema"]["schema"] == schema
    assert "private-key" not in repr(provider)
    assert CloudClient("groq", "model", "key", 2).is_cloud


def test_invalid_structured_response_is_rejected(monkeypatch):
    monkeypatch.setattr(
        HTTPProvider, "request", lambda *_args: {"message": {"content": '{"value":"wrong"}'}}
    )
    with pytest.raises(ProviderError, match="invalid structured"):
        OllamaClient("http://localhost:11434", "m", 2).generate(
            "task", {"type": "object", "properties": {"value": {"type": "integer"}}}
        )


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_http_failures_do_not_expose_response_body_or_key(monkeypatch, status):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, text="server echoed private-key")
    )
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    provider = HTTPProvider("model", "https://example.test/v1", api_key="private-key")
    with pytest.raises(ProviderUnavailable) as error:
        provider.generate("task")
    assert "private-key" not in str(error.value)


def test_timeout_and_missing_ollama_fall_back(monkeypatch):
    original = httpx.Client

    def timeout(request):
        raise httpx.ReadTimeout("private request details")

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(timeout), **kwargs)
    )
    with pytest.raises(ProviderTimeout, match="timed out"):
        OllamaClient("http://localhost:11434", "m", 2).generate("task")


def test_missing_api_key_never_sends_a_request(monkeypatch):
    monkeypatch.setattr(HTTPProvider, "request", lambda *_args: pytest.fail("request sent"))
    with pytest.raises(ProviderUnavailable, match="No API key"):
        HTTPProvider("m", "https://example.test/v1").generate("task")


@pytest.mark.parametrize(
    "failure", ["missing-key", "missing-model", "rate-limit", "timeout", "schema"]
)
def test_provider_failure_preserves_the_engine_template(tmp_path, monkeypatch, failure):
    from contextbridge.service import ProjectService

    service = ProjectService(tmp_path)
    service.init(install_hook=False)
    service.add_note("Goal: Build an offline Flutter lab helper")
    config = Config()
    config.llm.provider = "openai-compatible"
    config.llm.base_url = "https://example.test/v1"
    if failure != "missing-key":
        monkeypatch.setenv("CB_API_KEY", "test-only-key")

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private request details")
        status = {"missing-model": 404, "rate-limit": 429}.get(failure, 200)
        return httpx.Response(status, json={"choices": [{"message": {"content": '{"lines":123}'}}]})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    from contextbridge.synthesis.compressor import compress

    context = service.snapshot().context
    offline = compress(context, config, summary_budget=1500, no_llm=True)
    result = compress(context, config, summary_budget=1500)
    assert result.method == "deterministic" and result.summary == offline.summary
    assert result.attempts
    assert "test-only-key" not in str(result.attempts)
