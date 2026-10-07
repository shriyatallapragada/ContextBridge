from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextbridge.config import Config, global_config_path, load_config, save_config
from contextbridge.errors import ConfigError


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CB_OLLAMA_HOST")  # set by the autouse isolation fixture
    cfg = load_config()
    assert cfg == Config()
    assert cfg.llm.provider == "ollama"
    assert cfg.handoff.target_tokens == 400
    assert ".context/**" in cfg.capture.ignore_globs


def test_project_overrides_global(tmp_path: Path) -> None:
    save_config({"llm": {"model": "global-model", "timeout_seconds": 5}}, global_config_path())
    ctx = tmp_path / ".context"
    save_config({"llm": {"model": "project-model"}}, ctx / "config.json")
    cfg = load_config(ctx)
    assert cfg.llm.model == "project-model"
    assert cfg.llm.timeout_seconds == 5


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CB_LLM_MODEL", "llama3")
    monkeypatch.setenv("CB_CLOUD_FALLBACK", "true")
    cfg = load_config()
    assert cfg.llm.model == "llama3"
    assert cfg.llm.cloud_fallback is True


@pytest.mark.parametrize(
    "data",
    [
        {"llm": {"timeout_seconds": "60"}},
        {"llm": {"cloud_fallback": "yes"}},
        {"llm": "ollama"},
        {"handoff": {"target_tokens": 900, "hard_cap_tokens": 600}},
        {"capture": {"ignore_globs": [1, 2]}},
    ],
)
def test_invalid_config(tmp_path: Path, data: dict[str, object]) -> None:
    ctx = tmp_path / ".context"
    save_config(data, ctx / "config.json")
    with pytest.raises(ConfigError):
        load_config(ctx)


def test_malformed_json(tmp_path: Path) -> None:
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "config.json").write_text("{not json")
    with pytest.raises(ConfigError):
        load_config(ctx)


def test_save_strips_secret_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config({"llm": {"model": "m", "api_key": "sk-123", "groq_token": "t"}}, path)
    assert json.loads(path.read_text()) == {"llm": {"model": "m"}}


def test_save_keeps_token_budget_settings(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    data = {"handoff": {"target_tokens": 300, "hard_cap_tokens": 500}}
    save_config(data, path)
    assert json.loads(path.read_text()) == data
