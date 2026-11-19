"""Credential isolation, setup transactions and explicit provider selection."""

import sys
import types

import pytest
from typer.testing import CliRunner

from contextbridge.config import global_config_path, load_config, save_config
from contextbridge.errors import ConfigError, UserError
from contextbridge.main import app
from contextbridge.providers import credentials
from contextbridge.providers.settings import ProviderSettingsService


@pytest.fixture
def secure_store(monkeypatch):
    class Native:
        __module__ = "keyring.backends.macOS"
        priority = 5

        def __init__(self):
            self.values = {}

        def get_password(self, service, account):
            return self.values.get((service, account))

        def set_password(self, service, account, value):
            self.values[service, account] = value

        def delete_password(self, service, account):
            del self.values[service, account]

    backend = Native()
    import keyring

    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    return backend


def test_api_key_is_in_os_backend_only_and_scoped_to_endpoint(tmp_path, secure_store):
    service = ProviderSettingsService(tmp_path)
    service.save(
        "openai-compatible", "chosen-model", "https://example.test/v1", "private-key-value"
    )
    data = service.path.read_text()
    assert "private-key-value" not in data and "api_key" not in data
    assert credentials.get_key("https://example.test/v1") == "private-key-value"
    assert credentials.get_key("https://other.test/v1") is None
    assert service.load().llm.provider == "openai-compatible"
    assert service.load().llm.setup_complete


def test_no_plaintext_backend_or_file_fallback(tmp_path, monkeypatch):
    import keyring

    backend = types.SimpleNamespace(priority=10)
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    with pytest.raises(UserError, match="secure OS keyring"):
        ProviderSettingsService(tmp_path).save(
            "openai-compatible", "m", "https://example.test/v1", "secret"
        )
    assert not (tmp_path / "config.json").exists()


def test_config_failure_restores_previous_credential(tmp_path, secure_store, monkeypatch):
    import contextbridge.providers.settings as module

    endpoint = "https://example.test/v1"
    credentials.set_key(endpoint, "old-key")
    monkeypatch.setattr(
        module, "save_config", lambda *_args: (_ for _ in ()).throw(OSError("disk error"))
    )
    with pytest.raises(UserError, match="could not be saved"):
        ProviderSettingsService(tmp_path).save("openai-compatible", "m", endpoint, "new-key")
    assert credentials.get_key(endpoint) == "old-key"
    assert not (tmp_path / "config.json").exists()


def test_local_and_offline_setup_never_touch_keyring(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "set_key", lambda *_: pytest.fail("keyring write"))
    save_config(
        {"llm": {"cloud_fallback": True}, "capture": {"max_files_per_handoff": 7}},
        tmp_path / "config.json",
    )
    service = ProviderSettingsService(tmp_path)
    service.save("ollama", "my-local-model", "http://localhost:11434")
    assert service.load().llm.num_ctx == 16384
    assert not service.load().llm.cloud_fallback
    service.save("none")
    assert service.load().llm.provider == "none"
    assert service.load().capture.max_files_per_handoff == 7


@pytest.mark.parametrize(
    "endpoint", ["https://key:secret@example.test/v1", "https://example.test/v1?key=secret"]
)
def test_credentials_in_endpoint_are_rejected(tmp_path, endpoint):
    with pytest.raises(ConfigError):
        ProviderSettingsService(tmp_path).save("openai-compatible", "m", endpoint, "key")
    assert not (tmp_path / "config.json").exists()


def test_cli_hidden_key_prompt_and_global_offline_setup(secure_store):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "configure",
            "--global",
            "--provider",
            "openai-compatible",
            "--model",
            "m",
            "--endpoint",
            "https://example.test/v1",
            "--set-key",
        ],
        input="test-private-key\n",
    )
    assert result.exit_code == 0, result.output
    assert "test-private-key" not in result.output
    assert "test-private-key" not in global_config_path().read_text()
    result = runner.invoke(app, ["configure", "--global"])
    assert result.exit_code == 0 and load_config().llm.provider == "none"


def test_engine_works_with_all_model_sdks_unavailable(tmp_path, monkeypatch):
    for name in ("ollama", "openai", "groq"):
        monkeypatch.setitem(sys.modules, name, None)
    from contextbridge.service import ProjectService

    service = ProjectService(tmp_path)
    service.init(install_hook=False)
    service.add_note("Offline notes work")
    assert service.prepare_handoff(to_agent="codex").handoff.method == "deterministic"
