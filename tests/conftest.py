from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

# GUI tests render without a screen. Must be set before Qt is first imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolate_config(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never read or write the developer's real ~/.config during tests."""
    monkeypatch.setenv("CB_CONFIG_DIR", str(tmp_path_factory.mktemp("cfg")))
    for var in (
        "CB_LLM_PROVIDER",
        "CB_LLM_MODEL",
        "CB_OLLAMA_HOST",
        "OLLAMA_HOST",
        "CB_CLOUD_FALLBACK",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    # Never talk to a real local Ollama: port 9 (discard) refuses connections at once.
    monkeypatch.setenv("CB_OLLAMA_HOST", "http://127.0.0.1:9")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh git repo on branch `main` with one commit; cwd is set to it."""
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    monkeypatch.chdir(repo)
    yield repo
