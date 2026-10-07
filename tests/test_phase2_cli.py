"""CLI tests for Phase 2: `cb hook post-commit`, `cb run`, and hook install in `cb init`."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from contextbridge.core.git_observer import HOOK_START
from contextbridge.main import app
from tests.helpers import commit_all, git, write

runner = CliRunner()


def cb(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def _rows(repo: Path, sql: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(repo / ".context" / "state.db")
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _changes(repo: Path) -> list[dict[str, Any]]:
    rows = _rows(repo, "SELECT * FROM file_changes ORDER BY rowid")
    return [{**dict(r), "symbols": json.loads(r["symbols_modified"] or "[]")} for r in rows]


def _hook_file(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "post-commit"


SERVICE_PY = """\
def create_user(name):
    return {"name": name}


def delete_user(user_id):
    return True


class Billing:
    def charge(self, amount):
        return amount
"""


@pytest.fixture
def initialized(git_repo: Path) -> Path:
    write(git_repo, "service.py", SERVICE_PY)
    commit_all(git_repo, "add service")
    code, out = cb("init", "--no-hooks")
    assert code == 0, out
    commit_all(git_repo, "ignore .context/")  # init added it to .gitignore
    return git_repo


# ----- acceptance: commit editing one function records that function -----------------


def test_acceptance_commit_records_edited_function(initialized: Path) -> None:
    repo = initialized
    write(
        repo,
        "service.py",
        SERVICE_PY.replace('return {"name": name}', 'return {"name": name.strip()}'),
    )
    sha = commit_all(repo, "strip names")

    code, out = cb("hook", "post-commit")
    assert code == 0 and out == ""  # silent

    [change] = _changes(repo)
    assert change["commit_sha"] == sha
    assert change["file_path"] == "service.py"
    assert change["change_type"] == "modified"
    assert change["symbols"] == [
        {
            "name": "create_user",
            "kind": "function",
            "change": "modified",
            "signature": "def create_user(name)",
        }
    ]
    assert "@@" in change["diff_snippet"]
    assert (
        change["session_id"]
        == json.loads((repo / ".context" / "state.json").read_text())["active_session_id"]
    )


def test_hook_is_idempotent_per_commit(initialized: Path) -> None:
    write(initialized, "service.py", SERVICE_PY.replace("return True", "return False"))
    commit_all(initialized)
    assert cb("hook", "post-commit")[0] == 0
    assert cb("hook", "post-commit")[0] == 0
    assert len(_changes(initialized)) == 1


def test_hook_backfills_commits_since_last_recorded(initialized: Path) -> None:
    write(initialized, "a.py", "def a():\n    return 1\n")
    first = commit_all(initialized, "one")
    assert cb("hook", "post-commit")[0] == 0
    # Two commits without the hook running (as during a rebase), then one hook run.
    write(initialized, "b.py", "def b():\n    return 2\n")
    second = commit_all(initialized, "two")
    write(initialized, "c.py", "def c():\n    return 3\n")
    third = commit_all(initialized, "three")
    assert cb("hook", "post-commit")[0] == 0
    changes = _changes(initialized)
    assert [(c["commit_sha"], c["file_path"]) for c in changes] == [
        (first, "a.py"),
        (second, "b.py"),
        (third, "c.py"),
    ]
    assert changes[1]["symbols"][0] == {
        "name": "b",
        "kind": "function",
        "change": "added",
        "signature": "def b()",
    }


def test_first_hook_run_does_not_record_pre_init_commits(git_repo: Path) -> None:
    write(git_repo, "old.py", "x = 1\n")
    commit_all(git_repo, "before init")
    assert cb("init", "--no-hooks")[0] == 0
    write(git_repo, "new.py", "y = 2\n")
    commit_all(git_repo, "after init")
    cb("hook", "post-commit")
    assert [c["file_path"] for c in _changes(git_repo)] == [".gitignore", "new.py"]


def test_acceptance_binary_and_ignored_files_skipped(initialized: Path) -> None:
    repo = initialized
    write(repo, "image.png", b"\x89PNG\r\n\x1a\n\x00\x00binary\x00")
    write(repo, "package.lock", "{}")
    write(repo, "node_modules/lib/index.js", "module.exports = {}\n")
    write(repo, "dist/bundle.js", "var x = 1;\n")
    write(repo, ".env", "SECRET_KEY=hunter2\n")
    write(repo, "app.py", "def main():\n    pass\n")
    commit_all(repo)
    assert cb("hook", "post-commit")[0] == 0
    assert [c["file_path"] for c in _changes(repo)] == ["app.py"]


def test_custom_ignore_globs_from_config(initialized: Path) -> None:
    (initialized / ".context" / "config.json").write_text(
        json.dumps({"capture": {"ignore_globs": ["generated/**"]}})
    )
    write(initialized, "generated/api.py", "x = 1\n")
    write(initialized, "real.py", "y = 2\n")
    commit_all(initialized)
    cb("hook", "post-commit")
    assert [c["file_path"] for c in _changes(initialized)] == ["real.py"]


def test_diff_snippet_sanitized_and_truncated(initialized: Path) -> None:
    (initialized / ".context" / "config.json").write_text(
        json.dumps({"capture": {"max_diff_chars_per_file": 300}})
    )
    body = "".join(f"value_{i} = {i}\n" for i in range(200))
    write(initialized, "settings.py", 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n' + body)
    commit_all(initialized)
    cb("hook", "post-commit")
    [change] = _changes(initialized)
    snippet = change["diff_snippet"]
    assert "AKIAIOSFODNN7EXAMPLE" not in snippet
    assert "[REDACTED:aws_access_key]" in snippet
    assert "@@ -0,0 +1,201 @@" in snippet
    assert "truncated" in snippet
    assert len(snippet) < 600


def test_hook_rename_records_new_path(initialized: Path) -> None:
    git(initialized, "mv", "service.py", "users.py")
    commit_all(initialized, "rename")
    cb("hook", "post-commit")
    [change] = _changes(initialized)
    assert (change["file_path"], change["change_type"]) == ("users.py", "renamed")
    assert change["symbols"] == []  # pure rename: no symbol changed


def test_branch_change_starts_new_session(initialized: Path) -> None:
    repo = initialized
    state_path = repo / ".context" / "state.json"
    main_session = json.loads(state_path.read_text())["active_session_id"]
    git(repo, "checkout", "-q", "-b", "feature/x")
    write(repo, "f.py", "def f():\n    pass\n")
    commit_all(repo)
    cb("hook", "post-commit")
    state = json.loads(state_path.read_text())
    assert state["active_branch"] == "feature/x"
    assert state["active_session_id"] != main_session
    [change] = _changes(repo)
    assert change["session_id"] == state["active_session_id"]

    git(repo, "checkout", "-q", "main")
    assert cb("log", "back on main")[0] == 0
    assert json.loads(state_path.read_text())["active_session_id"] == main_session


def test_hook_never_fails_loudly(initialized: Path) -> None:
    write(initialized, "x.py", "x = 1\n")
    commit_all(initialized)
    (initialized / ".context" / "config.json").write_text("{not json")
    code, out = cb("hook", "post-commit")
    assert (code, out) == (0, "")
    assert "hook post-commit failed" in (initialized / ".context" / "cb.log").read_text()


def test_hook_outside_initialized_repo_is_silent(git_repo: Path) -> None:
    assert cb("hook", "post-commit") == (0, "")
    assert not (git_repo / ".context").exists()


def test_hook_skips_during_rebase(initialized: Path) -> None:
    (initialized / ".git" / "rebase-merge").mkdir()
    write(initialized, "x.py", "x = 1\n")
    commit_all(initialized)
    assert cb("hook", "post-commit")[0] == 0
    assert _changes(initialized) == []


def test_hook_fast_on_1k_file_repo(initialized: Path) -> None:
    for i in range(1000):
        write(initialized, f"pkg/mod_{i}.py", f"def f_{i}():\n    return {i}\n")
    commit_all(initialized, "bulk")
    cb("hook", "post-commit")  # record the bulk commit first
    write(initialized, "pkg/mod_7.py", "def f_7():\n    return 'seven'\n")
    commit_all(initialized, "edit")
    start = time.perf_counter()
    assert cb("hook", "post-commit")[0] == 0
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"hook took {elapsed:.2f}s"
    last = _changes(initialized)[-1]
    assert last["file_path"] == "pkg/mod_7.py"
    assert last["symbols"][0]["name"] == "f_7"


# ----- hook installation via `cb init` ---------------------------------------------------


def test_init_installs_hook(git_repo: Path) -> None:
    code, out = cb("init")
    assert code == 0, out
    assert "Installed git post-commit hook" in out
    assert HOOK_START in _hook_file(git_repo).read_text()
    code, out = cb("init")
    assert "already installed" in out
    assert _hook_file(git_repo).read_text().count(HOOK_START) == 1


def test_init_no_hooks(git_repo: Path) -> None:
    code, out = cb("init", "--no-hooks")
    assert code == 0, out
    assert "skipped (--no-hooks)" in out
    assert not _hook_file(git_repo).exists()


def test_acceptance_existing_hook_preserved(git_repo: Path) -> None:
    existing = "#!/bin/sh\n# team hook\nnpx lint-staged\n"
    _hook_file(git_repo).write_text(existing)
    _hook_file(git_repo).chmod(0o755)
    code, out = cb("init")
    assert code == 0, out
    assert "existing post-commit hook" in out
    content = _hook_file(git_repo).read_text()
    assert content.startswith(existing)
    assert HOOK_START in content

    code, out = cb("init", "--uninstall-hooks")
    assert code == 0, out
    assert _hook_file(git_repo).read_text() == existing


def test_uninstall_hooks_removes_created_file(git_repo: Path) -> None:
    cb("init")
    code, out = cb("init", "--uninstall-hooks")
    assert code == 0 and "Removed hook file" in out
    assert not _hook_file(git_repo).exists()
    code, out = cb("init", "--uninstall-hooks")
    assert code == 0 and "No ContextBridge hook" in out


def test_init_respects_core_hooks_path(git_repo: Path) -> None:
    git(git_repo, "config", "core.hooksPath", "tools/hooks")
    code, out = cb("init")
    assert code == 0, out
    assert HOOK_START in (git_repo / "tools" / "hooks" / "post-commit").read_text()
    assert not _hook_file(git_repo).exists()


def test_real_commit_triggers_background_hook(git_repo: Path) -> None:
    """End to end: `git commit` runs the installed hook, which records the change."""
    if shutil.which("cb") is None:
        pytest.skip("`cb` not on PATH")
    write(git_repo, "service.py", SERVICE_PY)
    commit_all(git_repo, "add service")
    assert cb("init")[0] == 0
    write(git_repo, "service.py", SERVICE_PY.replace("return amount", "return amount * 100"))
    git(git_repo, "add", "-A")
    start = time.perf_counter()
    git(git_repo, "commit", "-q", "-m", "charge in cents")
    assert time.perf_counter() - start < 5  # the hook runs in the background
    deadline = time.time() + 20
    changes: list[dict[str, Any]] = []
    while time.time() < deadline and not changes:
        time.sleep(0.2)
        changes = _changes(git_repo)
    assert changes, "background hook did not record the commit"
    by_path = {c["file_path"]: c for c in changes}
    assert [s["name"] for s in by_path["service.py"]["symbols"]] == ["Billing.charge"]


# ----- cb run ------------------------------------------------------------------------------

SAMPLE_TESTS = """\
import pytest


def test_ok():
    assert True


def test_also_ok():
    assert 1 + 1 == 2


def test_broken():
    assert 2 + 2 == 5, "math is broken"


@pytest.mark.skip(reason="later")
def test_later():
    pass
"""


def _test_runs(repo: Path) -> list[sqlite3.Row]:
    return _rows(repo, "SELECT * FROM test_runs ORDER BY rowid")


def test_acceptance_cb_run_pytest_records_counts(initialized: Path) -> None:
    write(initialized, "suite/test_sample.py", SAMPLE_TESTS)
    pytest_cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "suite"]
    result = runner.invoke(app, ["run", "--", *pytest_cmd])
    assert result.exit_code == 1, result.output  # pytest's own exit code
    assert "1 failed, 2 passed, 1 skipped" in result.output  # streamed through
    [run] = _test_runs(initialized)
    assert (run["exit_code"], run["passed"], run["failed"], run["skipped"]) == (1, 2, 1, 1)
    failures = json.loads(run["failures"])
    assert failures[0]["test"] == "suite/test_sample.py::test_broken"
    assert "math is broken" in failures[0]["message"]
    assert "pytest -q" in run["command"]


def test_cb_run_with_plain_pytest_executable(initialized: Path) -> None:
    if shutil.which("pytest") is None:
        pytest.skip("pytest not on PATH")
    write(initialized, "suite/test_sample.py", SAMPLE_TESTS.replace("2 + 2 == 5", "2 + 2 == 4"))
    result = runner.invoke(app, ["run", "--", "pytest", "-q", "-p", "no:cacheprovider", "suite"])
    assert result.exit_code == 0, result.output
    [run] = _test_runs(initialized)
    assert (run["passed"], run["failed"], run["skipped"]) == (3, 0, 1)


def test_cb_run_unknown_command_stores_exit_code_and_tail(initialized: Path) -> None:
    script = "import sys\nfor i in range(40): print(f'step {i}')\nsys.exit(7)"
    result = runner.invoke(app, ["run", "--", sys.executable, "-c", script])
    assert result.exit_code == 7
    [run] = _test_runs(initialized)
    assert run["exit_code"] == 7
    assert run["passed"] is None and run["failed"] is None
    [entry] = json.loads(run["failures"])
    lines = entry["message"].splitlines()
    assert len(lines) == 20 and lines[-1] == "step 39"


def test_cb_run_sanitizes_output(initialized: Path) -> None:
    token = "ghp_" + "A1b2C3d4E5" * 4
    result = runner.invoke(app, ["run", "--", sys.executable, "-c", f"print('token {token}')"])
    assert result.exit_code == 0
    raw = (initialized / ".context" / "state.db").read_bytes()
    assert token.encode() not in raw
    assert token not in _test_runs(initialized)[0]["failures"]


def test_cb_run_without_init_still_runs(git_repo: Path) -> None:
    result = runner.invoke(app, ["run", "--", sys.executable, "-c", "print('hi')"])
    assert result.exit_code == 0
    assert "hi" in result.output
    assert "result not recorded" in result.output


def test_cb_run_command_not_found(initialized: Path) -> None:
    result = runner.invoke(app, ["run", "--", "no-such-binary-xyz"])
    assert result.exit_code == 127
    assert _test_runs(initialized)[0]["exit_code"] == 127


def test_cb_run_requires_a_command(initialized: Path) -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code != 0


def test_cb_run_as_real_subprocess(initialized: Path) -> None:
    """The console script streams output and propagates the exit code."""
    if shutil.which("cb") is None:
        pytest.skip("`cb` not on PATH")
    proc = subprocess.run(
        ["cb", "run", "--", sys.executable, "-c", "import sys; print('out'); sys.exit(4)"],
        cwd=initialized,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 4
    assert proc.stdout == "out\n"
    assert "recorded exit code 4" in proc.stderr


def test_concurrent_session_resolution_creates_one_session(initialized: Path) -> None:
    """Background hooks may overlap after a branch switch: only one session per branch."""
    from concurrent.futures import ThreadPoolExecutor

    from contextbridge.storage.workspace import Workspace

    git(initialized, "checkout", "-q", "-b", "race")
    ws = Workspace.locate(initialized)

    def resolve(_: int) -> str:
        with ws.open_db() as db:
            session, _created = ws.resolve_active_session(db, ws.load_state())
            return session.id

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = set(pool.map(resolve, range(16)))
    assert len(ids) == 1
    rows = _rows(initialized, "SELECT COUNT(*) AS n FROM sessions WHERE branch_name='race'")
    assert rows[0]["n"] == 1


def test_hook_records_large_modified_file_without_misclassifying(
    initialized: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextbridge.core.git_observer as go

    monkeypatch.setattr(go, "MAX_SOURCE_BYTES", 10)  # every blob counts as "too large"
    write(initialized, "service.py", SERVICE_PY.replace("return True", "return False"))
    commit_all(initialized)
    cb("hook", "post-commit")
    [change] = _changes(initialized)
    assert change["change_type"] == "modified"
    assert all(s["change"] == "modified" for s in change["symbols"])
