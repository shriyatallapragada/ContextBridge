"""LLM clients (Ollama, Groq, OpenAI) and provider routing.

Routing (first that works wins; the deterministic fallback is the compressor's job):
- ``provider = "ollama"``: Ollama, then the cloud provider if ``cloud_fallback`` is on
  and its API key is set.
- ``provider = "groq" | "openai"``: that provider (needs its API key).
- ``provider = "none"`` or ``--no-llm``: no clients.

Callers must pass only sanitized text. API keys come from the environment only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from contextbridge.config import API_KEY_ENV, Config, api_key_for

log = logging.getLogger("contextbridge")

TEMPERATURE = 0.2


class LLMClientError(Exception):
    """A provider call failed; the router moves on to the next option."""


class LLMUnavailableError(LLMClientError):
    """The provider cannot be reached or is not set up (not running, model missing...)."""


class LLMTimeoutError(LLMClientError):
    """The provider did not answer within ``llm.timeout_seconds``."""


class LLMClient(Protocol):
    provider: str
    model: str

    @property
    def is_cloud(self) -> bool: ...

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        """Return the model's reply to ``user`` under ``system``."""
        ...


@dataclass
class OllamaClient:
    host: str
    model: str
    timeout: float
    provider: str = "ollama"

    @property
    def is_cloud(self) -> bool:
        return False

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        import httpx
        import ollama

        client = ollama.Client(host=self.host, timeout=self.timeout)
        try:
            response = client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                options={"temperature": TEMPERATURE, "num_predict": max_tokens},
            )
        except ConnectionError as exc:
            raise LLMUnavailableError(f"Ollama is not reachable at {self.host}") from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"Ollama timed out after {self.timeout:g}s") from exc
        except ollama.ResponseError as exc:
            if exc.status_code == 404:
                raise LLMUnavailableError(
                    f"model {self.model!r} is not available (run `ollama pull {self.model}`)"
                ) from exc
            raise LLMClientError(f"Ollama error: {exc.error}") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Ollama request failed: {type(exc).__name__}") from exc
        return str(response.message.content or "")


@dataclass
class CloudClient:
    """Groq or OpenAI via their (OpenAI-compatible) chat completions SDKs."""

    provider: str
    model: str
    api_key: str
    timeout: float

    @property
    def is_cloud(self) -> bool:
        return True

    def _sdk_client(self) -> Any:
        try:
            if self.provider == "groq":
                import groq

                return groq.Groq(api_key=self.api_key, timeout=self.timeout, max_retries=0)
            import openai

            return openai.OpenAI(api_key=self.api_key, timeout=self.timeout, max_retries=0)
        except ImportError as exc:
            raise LLMUnavailableError(
                f"the {self.provider} SDK is not installed (install contextbridge[cloud])"
            ) from exc

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        client = self._sdk_client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=TEMPERATURE,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name:
                raise LLMTimeoutError(f"{self.provider} timed out after {self.timeout:g}s") from exc
            if "Connection" in name or "Authentication" in name or "NotFound" in name:
                raise LLMUnavailableError(f"{self.provider}: {name}") from exc
            raise LLMClientError(f"{self.provider}: {name}") from exc
        content = response.choices[0].message.content if response.choices else None
        return str(content or "")


def _cloud_client(config: Config, provider: str) -> CloudClient | None:
    key = api_key_for(provider)
    if not key:
        log.info("llm: %s skipped, %s is not set", provider, API_KEY_ENV[provider])
        return None
    model = config.llm.cloud_model_for(provider)
    return CloudClient(provider, model, key, float(config.llm.timeout_seconds))


def candidates(config: Config, *, no_llm: bool = False) -> list[LLMClient]:
    """The LLM clients to try, in order."""
    if no_llm or config.llm.provider == "none":
        return []
    timeout = float(config.llm.timeout_seconds)
    if config.llm.provider == "ollama":
        clients: list[LLMClient] = [OllamaClient(config.llm.ollama_host, config.llm.model, timeout)]
        if config.llm.cloud_fallback:
            cloud = _cloud_client(config, config.llm.cloud_provider)
            if cloud is not None:
                clients.append(cloud)
        return clients
    cloud = _cloud_client(config, config.llm.provider)
    return [cloud] if cloud is not None else []
