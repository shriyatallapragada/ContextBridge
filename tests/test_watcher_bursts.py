"""Reproducible burst measurements, printed by pytest -s on one logical CPU."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from contextbridge.core.watcher import ProjectWatcher
from contextbridge.service import ProjectService
from tests.helpers import commit_all, git, write


def measure(root: Path, operation: Callable[[], None], label: str) -> dict:
    service = ProjectService(root)
    service.init(install_hook=False)
    watch = ProjectWatcher(service.ws)  # production defaults, including one-second polling
    ready, stop = threading.Event(), threading.Event()
    result: dict = {}
    errors: list[BaseException] = []

    def work() -> None:
        started, cpu = time.perf_counter(), time.thread_time()
        try:
            watch.run(stop, ready=ready)
        except BaseException as exc:
            errors.append(exc)
            ready.set()
        result.update(asdict(watch.metrics))
        result["wall_seconds"] = round(time.perf_counter() - started, 3)
        result["cpu_seconds"] = round(time.thread_time() - cpu, 6)
        result["cpu_percent_one_core"] = round(
            100 * result["cpu_seconds"] / result["wall_seconds"],
            3,
        )

    thread = threading.Thread(target=work)
    thread.start()
    assert ready.wait(10), "watch baseline did not finish"
    try:
        operation()
        time.sleep(4)  # allow one poll and the default two-second debounce
    finally:
        stop.set()
        thread.join(15)
    assert not thread.is_alive()
    assert not errors, errors
    print("\nWATCH_BENCH " + json.dumps({"scenario": label, **result}, sort_keys=True))
    return result


def test_idle_and_formatter_150_files(git_repo: Path) -> None:
    for number in range(150):
        write(git_repo, f"src/file_{number}.py", f'value={{"x":{number},"y":2}}\n')
    commit_all(git_repo, "150 unformatted files")
    idle = measure(git_repo, lambda: None, "idle, 150 files")
    assert idle["events"] == idle["batches"] == 0
    ruff = Path(sys.executable).parent / ("ruff.exe" if os.name == "nt" else "ruff")
    assert ruff.is_file(), "dev environment requires ruff"

    def format_files() -> None:
        subprocess.run(
            [str(ruff), "format", str(git_repo / "src")], check=True, capture_output=True
        )

    stats = measure(git_repo, format_files, "ruff format, 150 files")
    assert stats["files"] == 150 and stats["batches"] == 1
    assert stats["events"] == 150


def test_git_checkout_and_local_pull_150_files(git_repo: Path, tmp_path: Path) -> None:
    for number in range(150):
        write(git_repo, f"src/file_{number}.py", f"value = {number}\n")
    commit_all(git_repo, "initial files")
    git(git_repo, "checkout", "-q", "-b", "other")
    for number in range(150):
        write(git_repo, f"src/file_{number}.py", f"value = {number + 1000}\n")
    commit_all(git_repo, "other branch")
    git(git_repo, "checkout", "-q", "main")
    checkout = measure(
        git_repo, lambda: git(git_repo, "checkout", "-q", "other"), "git checkout, 150 files"
    )
    assert checkout["events"] == 150 and checkout["batches"] == 1
    remote = tmp_path / "remote"
    subprocess.run(
        ["git", "clone", "-q", str(git_repo), str(remote)], check=True, capture_output=True
    )
    git(remote, "config", "user.email", "test@example.com")
    git(remote, "config", "user.name", "Test")
    for number in range(150):
        write(remote, f"src/file_{number}.py", f"value = {number + 2000}\n")
    commit_all(remote, "upstream change")
    pull = measure(
        git_repo,
        lambda: git(git_repo, "pull", "--ff-only", str(remote), "other"),
        "git pull from local repo, 150 files",
    )
    assert pull["events"] == 150 and pull["batches"] == 1


def test_real_uv_sync_ignored_environment(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv, "uv is required for the requested dependency burst measurement"
    root = tmp_path / "project"
    root.mkdir()
    wheel = tmp_path / "fixture_lib-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for number in range(150):
            archive.writestr(f"fixture_lib/module_{number}.py", f"value = {number}\n")
        archive.writestr(
            "fixture_lib-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: fixture-lib\nVersion: 1.0\n",
        )
        archive.writestr(
            "fixture_lib-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr("fixture_lib-1.0.dist-info/RECORD", "")
    write(
        root,
        "pyproject.toml",
        '[project]\nname="burst-fixture"\nversion="0.1.0"\n'
        'requires-python=">=3.11"\ndependencies=["fixture-lib"]\n'
        f'[tool.uv.sources]\nfixture-lib={{path="{wheel.as_posix()}"}}\n',
    )

    def sync() -> None:
        env = dict(
            os.environ,
            UV_CACHE_DIR=str(tmp_path / "cache"),
            UV_PYTHON=sys.executable,
            UV_PYTHON_DOWNLOADS="never",
        )
        result = subprocess.run(
            [uv, "sync", "--offline", "--no-index"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

    stats = measure(root, sync, "uv sync, local wheel containing 150 modules")
    assert len(list((root / ".venv").rglob("module_*.py"))) == 150
    assert stats["events"] == stats["batches"] == 0
