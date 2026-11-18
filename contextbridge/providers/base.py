"""Provider contract, safe error messages, and JSON response validation."""

from __future__ import annotations

import json
from typing import Any, Protocol

from contextbridge.core.sanitizer import sanitize


class ProviderError(Exception):
    """A model failed. Callers can use their deterministic output."""


class ProviderUnavailable(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


class Provider(Protocol):
    provider: str
    model: str

    @property
    def is_cloud(self) -> bool: ...

    def generate(self, prompt: str, schema: dict[str, Any] | None = None) -> str: ...


def validate_response(text: str, schema: dict[str, Any] | None) -> str:
    if schema is not None:
        import jsonschema  # type: ignore[import-untyped]

        try:
            jsonschema.validate(json.loads(text), schema)
        except (ValueError, jsonschema.ValidationError, jsonschema.SchemaError):
            raise ProviderError("The model returned invalid structured output.") from None
    return sanitize(text)


class NoneProvider:
    provider = "none"
    model = ""
    is_cloud = False

    def generate(self, prompt: str, schema: dict[str, Any] | None = None) -> str:
        return ""
