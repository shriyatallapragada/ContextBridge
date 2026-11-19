"""Compatibility imports for clients that used the original synthesis module."""

from contextbridge.providers import candidates
from contextbridge.providers.base import (
    Provider as LLMClient,
)
from contextbridge.providers.base import (
    ProviderError as LLMClientError,
)
from contextbridge.providers.base import (
    ProviderTimeout as LLMTimeoutError,
)
from contextbridge.providers.base import (
    ProviderUnavailable as LLMUnavailableError,
)
from contextbridge.providers.http import HTTPProvider


def OllamaClient(host: str, model: str, timeout: float) -> HTTPProvider:
    return HTTPProvider(model, host, timeout, provider="ollama")


def CloudClient(provider: str, model: str, api_key: str, timeout: float) -> HTTPProvider:
    from contextbridge.providers import DEFAULT_ENDPOINTS

    return HTTPProvider(
        model, DEFAULT_ENDPOINTS[provider], timeout, api_key=api_key, provider=provider
    )


__all__ = [
    "CloudClient",
    "LLMClient",
    "LLMClientError",
    "LLMTimeoutError",
    "LLMUnavailableError",
    "OllamaClient",
    "candidates",
]
