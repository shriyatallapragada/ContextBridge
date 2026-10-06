from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_config(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never read or write the developer's real ~/.config during tests."""
    monkeypatch.setenv("CB_CONFIG_DIR", str(tmp_path_factory.mktemp("cfg")))
    for var in ("CB_LLM_PROVIDER", "CB_LLM_MODEL", "CB_OLLAMA_HOST", "OLLAMA_HOST"):
        monkeypatch.delenv(var, raising=False)


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
