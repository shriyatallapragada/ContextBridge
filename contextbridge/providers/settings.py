"""UI-independent provider configuration and explicit connection checks."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from contextbridge.config import Config, global_config_path, load_config, save_config
from contextbridge.errors import ContextBridgeError, UserError
from contextbridge.providers import DEFAULT_ENDPOINTS, create_provider, credentials
from contextbridge.providers.base import ProviderError
from contextbridge.providers.http import HTTPProvider, validate_endpoint


@dataclass(frozen=True)
class ProviderStatus:
    ok: bool
    message: str


class ProviderSettingsService:
    def __init__(self, context_dir: Path | None = None) -> None:
        self.context_dir = context_dir

    def load(self) -> Config:
        return load_config(self.context_dir)

    @property
    def path(self) -> Path:
        return self.context_dir / "config.json" if self.context_dir else global_config_path()

    def save(
        self,
        provider: str,
        model: str = "",
        endpoint: str = "",
        api_key: str = "",
    ) -> None:
        config = self.load()
        config.llm.provider = provider
        config.llm.setup_complete = True
        if model.strip():
            config.llm.model = model.strip()
        if provider == "ollama":
            config.llm.ollama_host = validate_endpoint(
                endpoint or config.llm.ollama_host, local=True
            )
        if provider == "openai-compatible":
            config.llm.base_url = validate_endpoint(endpoint or config.llm.base_url)
            if not model.strip():
                raise UserError("Enter the model name supplied by your API provider.")
            if not api_key.strip() and not credentials.get_key(config.llm.base_url):
                raise UserError("Enter an API key to enable the API provider.")
        config.validate()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        except (OSError, ValueError):
            raise UserError(
                "The existing configuration could not be read. Correct it before saving."
            ) from None
        if not isinstance(data, dict):
            raise UserError("The configuration must contain a JSON object.")
        existing = data.get("llm", {})
        llm = dict(existing) if isinstance(existing, dict) else {}
        llm.update(provider=provider, setup_complete=True, cloud_fallback=False)
        if model.strip():
            llm["model"] = config.llm.model
        if provider == "ollama":
            llm.update(ollama_host=config.llm.ollama_host, num_ctx=config.llm.num_ctx)
        if provider == "openai-compatible":
            llm["base_url"] = config.llm.base_url
        data["llm"] = llm
        old_key = None
        if provider == "openai-compatible" and api_key.strip():
            old_key = credentials.get_key(config.llm.base_url)
            credentials.set_key(config.llm.base_url, api_key.strip())
        try:
            save_config(data, self.path)
        except OSError:
            if provider == "openai-compatible" and api_key.strip():
                if old_key is None:
                    credentials.delete_key(config.llm.base_url)
                else:
                    credentials.set_key(config.llm.base_url, old_key)
            raise UserError("The provider configuration could not be saved.") from None

    def check(self, provider: str, model: str, endpoint: str, api_key: str = "") -> ProviderStatus:
        if provider == "none":
            return ProviderStatus(True, "Offline mode is ready. No model or API key is needed.")
        try:
            config = self.load()
            config.llm.provider = provider
            config.llm.model = model
            if provider == "ollama":
                config.llm.ollama_host = validate_endpoint(endpoint, local=True)
            else:
                config.llm.base_url = validate_endpoint(endpoint)
                if DEFAULT_ENDPOINTS.get(provider) != config.llm.base_url:
                    config.llm.provider = "openai-compatible"
            selected = create_provider(config)
            assert isinstance(selected, HTTPProvider)
            if not model.strip():
                return ProviderStatus(False, "Enter the model name to check.")
            selected.model = model.strip()
            selected.endpoint = endpoint.rstrip("/")
            selected.max_tokens = 8
            selected.api_key = api_key or selected.api_key
            if selected.is_cloud and not selected.api_key:
                return ProviderStatus(False, "Enter an API key or save one in the OS keyring.")
            selected.generate("Reply with OK.")
            return ProviderStatus(
                True, f"Model {selected.model!r} responded successfully at {selected.endpoint}."
            )
        except ProviderError as exc:
            message = str(exc)
            if provider == "ollama" and not shutil.which("ollama"):
                message += " Ollama was not found on PATH. Install or open the Ollama app."
            return ProviderStatus(False, message)
        except ContextBridgeError as exc:
            return ProviderStatus(False, str(exc))
