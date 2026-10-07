"""LLM client routing and error mapping (SDKs are mocked; no network)."""

from __future__ import annotations

import sys
import types
from typing import Any

import httpx
import ollama
import pytest

from contextbridge.config import Config
from contextbridge.synthesis.llm_client import (
    CloudClient,
    LLMClientError,
    LLMTimeoutError,
    LLMUnavailableError,
    OllamaClient,
    candidates,
)


def _providers(cfg: Config, **kw: Any) -> list[tuple[str, str]]:
    return [(c.provider, c.model) for c in candidates(cfg, **kw)]


def test_default_routing_is_ollama_only() -> None:
    assert _providers(Config()) == [("ollama", "qwen2.5-coder:7b")]


def test_cloud_fallback_needs_flag_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config()
    cfg.llm.cloud_fallback = True
    assert _providers(cfg) == [("ollama", "qwen2.5-coder:7b")]  # no key yet
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    assert _providers(cfg) == [("ollama", "qwen2.5-coder:7b"), ("groq", "llama-3.1-8b-instant")]
    cfg.llm.cloud_fallback = False
    assert _providers(cfg) == [("ollama", "qwen2.5-coder:7b")]


def test_direct_cloud_provider_and_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config()
    cfg.llm.provider = "openai"
    assert _providers(cfg) == []  # no key: nothing to try
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert _providers(cfg) == [("openai", "gpt-4o-mini")]
    cfg.llm.cloud_model = "my-model"
    assert _providers(cfg) == [("openai", "my-model")]


def test_no_llm_and_none_provider() -> None:
    assert _providers(Config(), no_llm=True) == []
    cfg = Config()
    cfg.llm.provider = "none"
    assert _providers(cfg) == []


# ----- Ollama error mapping ---------------------------------------------------------------


class _FakeOllama:
    def __init__(self, exc: Exception | None = None, reply: str = "ok") -> None:
        self.exc, self.reply = exc, reply
        self.kwargs: dict[str, Any] = {}

    def __call__(self, host: str, timeout: float) -> _FakeOllama:
        self.kwargs = {"host": host, "timeout": timeout}
        return self

    def chat(self, **kwargs: Any) -> Any:
        self.kwargs.update(kwargs)
        if self.exc is not None:
            raise self.exc
        return types.SimpleNamespace(message=types.SimpleNamespace(content=self.reply))


def test_ollama_unreachable_for_real() -> None:
    """Port 9 refuses connections: the real client error maps to LLMUnavailableError."""
    with pytest.raises(LLMUnavailableError, match="not reachable"):
        OllamaClient("http://127.0.0.1:9", "m", 2).complete("s", "u", max_tokens=10)


@pytest.mark.parametrize(
    ("exc", "expected", "match"),
    [
        (httpx.ReadTimeout("slow"), LLMTimeoutError, "timed out"),
        (ollama.ResponseError("model not found", 404), LLMUnavailableError, "ollama pull"),
        (ollama.ResponseError("boom", 500), LLMClientError, "boom"),
        (httpx.RemoteProtocolError("bad"), LLMUnavailableError, "RemoteProtocolError"),
    ],
)
def test_ollama_error_mapping(
    monkeypatch: pytest.MonkeyPatch, exc: Exception, expected: type[Exception], match: str
) -> None:
    monkeypatch.setattr(ollama, "Client", _FakeOllama(exc))
    with pytest.raises(expected, match=match):
        OllamaClient("http://h", "m", 5).complete("s", "u", max_tokens=10)


def test_ollama_passes_timeout_model_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeOllama(reply="- summary")
    monkeypatch.setattr(ollama, "Client", fake)
    assert (
        OllamaClient("http://h:1", "qwen", 7).complete("sys", "usr", max_tokens=99) == "- summary"
    )
    assert fake.kwargs["timeout"] == 7 and fake.kwargs["model"] == "qwen"
    assert fake.kwargs["options"]["num_predict"] == 99
    assert [m["role"] for m in fake.kwargs["messages"]] == ["system", "user"]


# ----- cloud error mapping --------------------------------------------------------------------


def _fake_sdk(monkeypatch: pytest.MonkeyPatch, name: str, exc: Exception | None) -> None:
    def create(**kwargs: Any) -> Any:
        if exc is not None:
            raise exc
        msg = types.SimpleNamespace(content="- cloud summary")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    module = types.ModuleType(name)
    setattr(module, "Groq" if name == "groq" else "OpenAI", lambda **kw: client)
    monkeypatch.setitem(sys.modules, name, module)


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


@pytest.mark.parametrize("provider", ["groq", "openai"])
def test_cloud_success(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    _fake_sdk(monkeypatch, provider, None)
    client = CloudClient(provider, "m", "key", 5)
    assert client.is_cloud
    assert client.complete("s", "u", max_tokens=10) == "- cloud summary"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (APITimeoutError(), LLMTimeoutError),
        (APIConnectionError(), LLMUnavailableError),
        (ValueError("x"), LLMClientError),
    ],
)
def test_cloud_error_mapping(
    monkeypatch: pytest.MonkeyPatch, exc: Exception, expected: type[Exception]
) -> None:
    _fake_sdk(monkeypatch, "groq", exc)
    with pytest.raises(expected):
        CloudClient("groq", "m", "key", 5).complete("s", "u", max_tokens=10)


def test_cloud_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "groq", None)  # import groq -> ImportError
    with pytest.raises(LLMUnavailableError, match=r"contextbridge\[cloud\]"):
        CloudClient("groq", "m", "key", 5).complete("s", "u", max_tokens=10)
