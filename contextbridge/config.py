"""Configuration: defaults, global file, per-project override, env overrides.

Load order (later wins): built-in defaults -> global config
(``<user config dir>/contextbridge/config.json``) -> ``.context/config.json`` -> env vars.

API keys are never part of the config; they are read from the environment only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

from contextbridge.errors import ConfigError

APP_NAME = "contextbridge"
CONFIG_FILENAME = "config.json"
CONFIG_DIR_ENV = "CB_CONFIG_DIR"

LLM_PROVIDERS = ("ollama", "groq", "openai", "none")
CLOUD_PROVIDERS = ("groq", "openai")
API_KEY_ENV = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY"}

# Keys that must never be persisted to a config file, even if a user adds them.
# Anchored so that settings such as `target_tokens` are not caught.
_FORBIDDEN_KEY = re.compile(r"(?i)(api_?key|secret|password|passwd|(^|_)token)$")


@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = "qwen2.5-coder:7b"
    ollama_host: str = "http://localhost:11434"
    cloud_fallback: bool = False
    cloud_provider: str = "groq"
    timeout_seconds: int = 60


def _default_ignore_globs() -> list[str]:
    return ["*.lock", "node_modules/**", "dist/**", "*.min.js", ".context/**"]


@dataclass
class CaptureConfig:
    max_diff_chars_per_file: int = 4000
    max_files_per_handoff: int = 25
    ignore_globs: list[str] = field(default_factory=_default_ignore_globs)


@dataclass
class HandoffConfig:
    target_tokens: int = 400
    hard_cap_tokens: int = 600


@dataclass
class VectorConfig:
    enabled: bool = False


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    handoff: HandoffConfig = field(default_factory=HandoffConfig)
    vector: VectorConfig = field(default_factory=VectorConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if self.llm.provider not in LLM_PROVIDERS:
            raise ConfigError(
                f"Unknown llm.provider {self.llm.provider!r}; expected one of {LLM_PROVIDERS}."
            )
        if self.llm.cloud_provider not in CLOUD_PROVIDERS:
            raise ConfigError(
                f"Unknown llm.cloud_provider {self.llm.cloud_provider!r}; "
                f"expected one of {CLOUD_PROVIDERS}."
            )
        if self.handoff.target_tokens <= 0 or self.handoff.hard_cap_tokens <= 0:
            raise ConfigError("handoff token limits must be positive.")
        if self.handoff.target_tokens > self.handoff.hard_cap_tokens:
            raise ConfigError("handoff.target_tokens must not exceed handoff.hard_cap_tokens.")
        if self.capture.max_diff_chars_per_file <= 0 or self.capture.max_files_per_handoff <= 0:
            raise ConfigError("capture limits must be positive.")


def global_config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override)
    return Path(user_config_dir(APP_NAME, appauthor=False))


def global_config_path() -> Path:
    return global_config_dir() / CONFIG_FILENAME


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a JSON object.")
    return data


def _apply(target: Any, overrides: dict[str, Any], source: str) -> None:
    """Recursively apply ``overrides`` onto dataclass ``target``, checking types."""
    known = {f.name: f for f in fields(target)}
    for key, value in overrides.items():
        if key not in known:
            # Unknown keys are ignored for forward compatibility.
            continue
        current = getattr(target, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ConfigError(f"{source}: '{key}' must be an object.")
            _apply(current, value, source)
            continue
        expected: type = type(current)
        ok = isinstance(value, expected) and not (expected is int and isinstance(value, bool))
        if expected is list:
            ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        if not ok:
            raise ConfigError(f"{source}: '{key}' must be of type {expected.__name__}.")
        setattr(target, key, value)


def _env_overrides(cfg: Config) -> None:
    if provider := os.environ.get("CB_LLM_PROVIDER"):
        cfg.llm.provider = provider
    if model := os.environ.get("CB_LLM_MODEL"):
        cfg.llm.model = model
    if host := os.environ.get("CB_OLLAMA_HOST") or os.environ.get("OLLAMA_HOST"):
        cfg.llm.ollama_host = host
    if fallback := os.environ.get("CB_CLOUD_FALLBACK"):
        cfg.llm.cloud_fallback = fallback.strip().lower() in {"1", "true", "yes", "on"}


def load_config(project_context_dir: Path | None = None) -> Config:
    """Load the effective config for a project (or global-only if no project given)."""
    cfg = Config()
    gpath = global_config_path()
    _apply(cfg, _read_json(gpath), str(gpath))
    if project_context_dir is not None:
        ppath = project_context_dir / CONFIG_FILENAME
        _apply(cfg, _read_json(ppath), str(ppath))
    _env_overrides(cfg)
    cfg.validate()
    return cfg


def _strip_secrets(data: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in data.items():
        if _FORBIDDEN_KEY.search(key):
            continue
        clean[key] = _strip_secrets(value) if isinstance(value, dict) else value
    return clean


def save_config(data: dict[str, Any], path: Path) -> None:
    """Write a config (or partial override) to ``path``. Secret-like keys are dropped."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(_strip_secrets(data), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def api_key_for(provider: str) -> str | None:
    """Return the API key for a cloud provider from the environment, if set."""
    env_name = API_KEY_ENV.get(provider)
    return os.environ.get(env_name) if env_name else None
