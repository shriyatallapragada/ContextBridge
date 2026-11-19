"""Ollama and OpenAI-compatible HTTP transports with bounded requests."""

from __future__ import annotations

import contextlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from contextbridge.core.sanitizer import sanitize
from contextbridge.errors import ConfigError
from contextbridge.providers.base import (
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    validate_response,
)
from contextbridge.providers.limits import RateLimits


class ModelNotFound(ProviderUnavailable):
    """The selected model or generation route was rejected."""


def validate_endpoint(endpoint: str, *, local: bool = False) -> str:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigError(
            "Use an HTTP(S) endpoint without credentials, query parameters or fragments."
        )
    if local and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ConfigError(
            "Ollama local mode requires a loopback address. Use an API endpoint for hosted models."
        )
    return endpoint.rstrip("/")


@dataclass
class HTTPProvider:
    model: str
    endpoint: str
    timeout: float = 30.0
    max_tokens: int = 4096
    api_key: str = field(default="", repr=False)
    num_ctx: int = 16384
    provider: str = "openai-compatible"
    rate_limits: RateLimits = field(default_factory=RateLimits, repr=False)

    @property
    def reasoning_model(self) -> bool:
        return self.model.startswith(("openai/gpt-oss-", "gpt-oss-", "o1", "o3", "o4"))

    @property
    def is_cloud(self) -> bool:
        return self.provider != "ollama"

    def request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        endpoint = validate_endpoint(self.endpoint, local=not self.is_cloud)
        headers = {"Authorization": "Bearer " + self.api_key} if self.api_key else {}
        try:
            # Explicit endpoints are reached directly, so inherited proxy credentials
            # cannot redirect local project data to a third-party proxy.
            with (
                httpx.Client(
                    timeout=self.timeout, trust_env=False, follow_redirects=False
                ) as client,
                client.stream(
                    "GET" if payload is None else "POST",
                    endpoint + path,
                    json=payload,
                    headers=headers,
                ) as response,
            ):
                self.rate_limits.update(dict(response.headers), time.time())
                if response.status_code == 429:
                    retry_after = None
                    try:
                        retry_after = max(0.0, float(response.headers["Retry-After"]))
                    except (KeyError, ValueError):
                        from datetime import UTC, datetime
                        from email.utils import parsedate_to_datetime

                        with contextlib.suppress(KeyError, ValueError, TypeError, OverflowError):
                            retry_after = max(
                                0.0,
                                (
                                    parsedate_to_datetime(response.headers["Retry-After"])
                                    - datetime.now(UTC)
                                ).total_seconds(),
                            )
                    delay = retry_after or self.rate_limits.wait(1, 0, time.time())
                    raise ProviderRateLimited(
                        "The provider rate limit was reached. "
                        f"Retry after {delay:.1f}s. "
                        "Using the offline result.",
                        retry_after,
                    )
                if response.status_code in (401, 403):
                    raise ProviderUnavailable(
                        "The provider rejected the API key. Check it in Settings."
                    )
                if response.status_code == 404:
                    raise ModelNotFound("The endpoint or model was not found.")
                if response.status_code in (400, 410):
                    # Inspect only known error codes, never echo a server's error body.
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 2_000_000:
                            raise ProviderError("The provider response was too large.")
                    try:
                        error = json.loads(content).get("error", {})
                    except (ValueError, AttributeError):
                        error = {}
                    if isinstance(error, dict) and error.get("code") in (
                        "model_not_found",
                        "model_not_available",
                        "model_decommissioned",
                    ):
                        raise ModelNotFound("The endpoint or model was not found.")
                if not 200 <= response.status_code < 300:
                    raise ProviderUnavailable(
                        "The model service is unavailable. Using the offline result."
                    )
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ProviderError("The provider response was too large.")
                    chunks.append(chunk)
                result = json.loads(b"".join(chunks))
                if not isinstance(result, dict):
                    raise ValueError("object required")
                return result
        except httpx.TimeoutException:
            raise ProviderTimeout(
                "The model request timed out. Using the offline result."
            ) from None
        except httpx.HTTPError:
            message = (
                "Ollama isn't running. Open Ollama, or continue in offline mode."
                if not self.is_cloud
                else "The API endpoint could not be reached. Using the offline result."
            )
            raise ProviderUnavailable(message) from None
        except (ValueError, UnicodeError):
            raise ProviderError("The provider returned an unreadable response.") from None

    def _safe_detail(self, text: str) -> str:
        return sanitize(text.replace(self.api_key, "[REDACTED]") if self.api_key else text)

    def _missing_model_message(self) -> str:
        route = "/chat/completions" if self.is_cloud else "/api/chat"
        listing = "/models" if self.is_cloud else "/api/tags"
        prefix = (
            f"Model {self.model!r} or endpoint was not found. "
            f"Tried {self.endpoint.rstrip('/') + route}. "
        )
        try:
            response = self.request(listing)
            entries = response.get("data" if self.is_cloud else "models")
            if not isinstance(entries, list):
                raise ProviderError("The model list was unreadable.")
            names = sorted(
                {
                    str(item.get("id") if self.is_cloud else item.get("name", item.get("model")))[
                        :200
                    ]
                    for item in entries
                    if isinstance(item, dict)
                    and isinstance(
                        item.get("id") if self.is_cloud else item.get("name", item.get("model")),
                        str,
                    )
                }
            )
            offered = ", ".join(names[:50]) or "none returned"
            if len(names) > 50:
                offered += f" (and {len(names) - 50} more)"
            detail = f"Offered models: {offered}."
        except (ProviderError, ConfigError):
            detail = (
                f"Offered models could not be retrieved from {self.endpoint.rstrip('/') + listing}."
            )
        if not self.is_cloud:
            detail += f" Install the model with ollama pull {self.model}."
        return self._safe_detail(prefix + detail)

    def generate(self, prompt: str, schema: dict[str, Any] | None = None) -> str:
        try:
            return self._generate(prompt, schema)
        except ModelNotFound:
            raise ModelNotFound(self._missing_model_message()) from None

    def _generate(self, prompt: str, schema: dict[str, Any] | None) -> str:
        if self.is_cloud and not self.api_key:
            raise ProviderUnavailable(
                "No API key is configured. Add one in Settings or use offline mode."
            )
        messages = [{"role": "user", "content": sanitize(prompt)}]
        if self.provider == "ollama":
            body: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "num_predict": self.max_tokens,
                    "num_ctx": self.num_ctx,
                },
            }
            if schema is not None:
                body["format"] = schema
            result = self.request("/api/chat", body)
            try:
                content = result["message"]["content"]
            except (KeyError, TypeError):
                raise ProviderError("Ollama returned no model response.") from None
        else:
            body = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": self.max_tokens,
            }
            if self.reasoning_model:
                body.pop("max_tokens")
                body["max_completion_tokens"] = self.max_tokens
                if urlsplit(self.endpoint).hostname in {"api.groq.com", "api.openai.com"}:
                    body["reasoning_effort"] = "low"
                if urlsplit(self.endpoint).hostname == "api.groq.com":
                    body["include_reasoning"] = False
            if schema is not None:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "contextbridge_response",
                        "strict": True,
                        "schema": schema,
                    },
                }
            result = self.request("/chat/completions", body)
            try:
                choice = result["choices"][0]
                content = choice["message"].get("content")
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict)
                        and part.get("type") in {"text", "output_text"}
                        and isinstance(part.get("text"), str)
                    )
                if isinstance(content, str):
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S | re.I).strip()
                    if "<think>" in content.lower():
                        content = ""  # An unfinished reasoning block is never a final answer.
            except (KeyError, IndexError, TypeError, AttributeError):
                raise ProviderError("The provider returned no model response.") from None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(
                self._safe_detail(
                    f"Model {self.model!r} returned no final answer (empty model response). "
                    "Reasoning-only output is not a successful response. "
                    f"Output budget: {self.max_tokens} tokens; try a larger completion budget."
                )
            )
        return validate_response(content, schema)

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        """Compatibility entry point for earlier integrations."""
        from dataclasses import replace

        return replace(self, max_tokens=max_tokens).generate(system + "\n\n" + user)
