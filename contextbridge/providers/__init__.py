"""Optional providers behind the generate(prompt, schema=None) interface."""

from __future__ import annotations

import os

from contextbridge.config import API_KEY_ENV, Config
from contextbridge.providers import credentials
from contextbridge.providers.base import (
    NoneProvider,
    Provider,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)
from contextbridge.providers.http import HTTPProvider

DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
}


def create_provider(
    config: Config, provider: str | None = None, *, read_credentials: bool = True
) -> Provider:
    cfg = config.llm
    name = provider or cfg.provider
    if name == "none":
        return NoneProvider()
    if name == "ollama":
        return HTTPProvider(
            cfg.model, cfg.ollama_host, cfg.timeout_seconds, num_ctx=cfg.num_ctx, provider="ollama"
        )
    endpoint = DEFAULT_ENDPOINTS.get(name, cfg.base_url)
    key = (
        (credentials.get_key(endpoint) or os.environ.get(API_KEY_ENV.get(name, "CB_API_KEY"), ""))
        if read_credentials
        else ""
    )
    model = cfg.cloud_model_for(name) if name in DEFAULT_ENDPOINTS else cfg.model
    return HTTPProvider(model, endpoint, cfg.timeout_seconds, api_key=key, provider=name)


def candidates(config: Config, *, no_llm: bool = False) -> list[Provider]:
    if no_llm or config.llm.provider == "none":
        return []
    result = [create_provider(config)]
    if config.llm.provider == "ollama" and config.llm.cloud_fallback:
        result.append(create_provider(config, config.llm.cloud_provider))
    return result


__all__ = [
    "HTTPProvider",
    "NoneProvider",
    "Provider",
    "ProviderError",
    "ProviderTimeout",
    "ProviderUnavailable",
    "candidates",
    "create_provider",
]
