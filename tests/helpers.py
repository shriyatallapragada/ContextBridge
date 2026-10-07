"""Helpers for tests that drive real temporary git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return proc.stdout


def write(repo: Path, rel: str, content: str | bytes) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return path


def commit_all(repo: Path, message: str = "change") -> str:
    """Stage everything and commit (hooks disabled); return the new HEAD sha."""
    git(repo, "add", "-A")
    git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()
