from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from contextbridge.core.git_observer import (
    HOOK_END,
    HOOK_START,
    ChangeType,
    GitObserver,
    HookAction,
    matches_ignore,
    truncate_diff,
)
from contextbridge.errors import NotAGitRepoError
from tests.helpers import commit_all, git, write

IGNORE = ["*.lock", "node_modules/**", "dist/**", "*.min.js", ".context/**"]


@pytest.fixture
def obs(git_repo: Path) -> GitObserver:
    return GitObserver.open(git_repo)


def _diff(obs: GitObserver, sha: str, max_chars: int = 4000):  # type: ignore[no-untyped-def]
    return obs.commit_changes(sha, ignore_globs=IGNORE, max_diff_chars=max_chars)


# ----- repository state ---------------------------------------------------------


def test_open_finds_root_from_subdir(git_repo: Path) -> None:
    sub = git_repo / "a" / "b"
    sub.mkdir(parents=True)
    assert GitObserver.open(sub).root == git_repo.resolve()


def test_not_a_repo(tmp_path: Path) -> None:
    with pytest.raises(NotAGitRepoError):
        GitObserver.open(tmp_path)


def test_branch_and_head(obs: GitObserver, git_repo: Path) -> None:
    state = obs.repo_state()
    assert state.branch == "main"
    assert state.head_sha == git(git_repo, "rev-parse", "HEAD").strip()
    assert state.has_commits and not state.detached and state.operation is None


def test_detached_head(obs: GitObserver, git_repo: Path) -> None:
    git(git_repo, "checkout", "-q", "--detach")
    state = obs.repo_state()
    assert state.branch is None and state.detached


def test_no_commits_yet(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "trunk")
    obs = GitObserver.open(repo)
    state = obs.repo_state()
    assert state.branch == "trunk" and not state.has_commits
    assert obs.commits_since("2000-01-01T00:00:00Z") == []
    write(repo, "a.txt", "x")
    assert obs.status().untracked == ["a.txt"]


def test_merge_in_progress(obs: GitObserver, git_repo: Path) -> None:
    git(git_repo, "checkout", "-q", "-b", "feature")
    write(git_repo, "README.md", "feature\n")
    commit_all(git_repo, "feature")
    git(git_repo, "checkout", "-q", "main")
    write(git_repo, "README.md", "main\n")
    commit_all(git_repo, "main")
    proc = os.system(f"git -C '{git_repo}' merge -q feature >/dev/null 2>&1")
    assert proc != 0  # conflict leaves the merge in progress
    assert obs.repo_state().operation == "merge"


def test_status_staged_unstaged_untracked(obs: GitObserver, git_repo: Path) -> None:
    write(git_repo, "staged.txt", "s")
    git(git_repo, "add", "staged.txt")
    write(git_repo, "README.md", "changed\n")
    write(git_repo, "new dir/untracked file.txt", "u")
    status = obs.status()
    assert status.staged == ["staged.txt"]
    assert status.unstaged == ["README.md"]
    assert status.untracked == ["new dir/untracked file.txt"]
    assert not status.clean


def test_commits_since(obs: GitObserver, git_repo: Path) -> None:
    write(git_repo, "a.txt", "1")
    first = commit_all(git_repo, "first change")
    write(git_repo, "a.txt", "2")
    second = commit_all(git_repo, "second change")
    commits = obs.commits_since("2000-01-01T00:00:00Z")
    assert [c.sha for c in commits[:2]] == [second, first]
    assert commits[0].subject == "second change"
    assert obs.commits_since("2999-01-01T00:00:00Z") == []


# ----- diffs ------------------------------------------------------------------------


def test_commit_changes_types(obs: GitObserver, git_repo: Path) -> None:
    write(git_repo, "keep.py", "a = 1\n")
    write(git_repo, "gone.py", "b = 2\n")
    commit_all(git_repo)
    write(git_repo, "keep.py", "a = 2\n")
    (git_repo / "gone.py").unlink()
    write(git_repo, "new.py", "c = 3\n")
    sha = commit_all(git_repo)
    by_path = {f.path: f for f in _diff(obs, sha).files}
    assert by_path["keep.py"].change_type == ChangeType.modified
    assert by_path["gone.py"].change_type == ChangeType.deleted
    assert by_path["new.py"].change_type == ChangeType.added
    assert "+a = 2" in by_path["keep.py"].patch


def test_root_commit(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    write(repo, "a.py", "x = 1\n")
    sha = commit_all(repo, "root")
    files = _diff(GitObserver.open(repo), sha).files
    assert [(f.path, f.change_type) for f in files] == [("a.py", ChangeType.added)]


def test_rename_detection(obs: GitObserver, git_repo: Path) -> None:
    body = "".join(f"line {i}\n" for i in range(30))
    write(git_repo, "old_name.py", body)
    commit_all(git_repo)
    (git_repo / "pkg").mkdir()
    git(git_repo, "mv", "old_name.py", "pkg/new_name.py")
    write(git_repo, "pkg/new_name.py", body + "line 30\n")
    sha = commit_all(git_repo)
    [change] = _diff(obs, sha).files
    assert change.change_type == ChangeType.renamed
    assert change.old_path == "old_name.py"
    assert change.path == "pkg/new_name.py"
    assert "+line 30" in change.patch


def test_binary_ignored_and_sensitive_files_skipped(obs: GitObserver, git_repo: Path) -> None:
    write(git_repo, "logo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00binary\x00data")
    write(git_repo, "uv.lock", "lock")
    write(git_repo, "node_modules/x/index.js", "module.exports = 1")
    write(git_repo, "web/node_modules/y.js", "1")
    write(git_repo, ".env.local", "API_KEY=abc")
    write(git_repo, "app.py", "print('hi')\n")
    sha = commit_all(git_repo)
    result = _diff(obs, sha)
    assert [f.path for f in result.files] == ["app.py"]
    reasons = {s.path: s.reason for s in result.skipped}
    assert reasons == {
        "logo.png": "binary",
        "uv.lock": "ignored",
        "node_modules/x/index.js": "ignored",
        "web/node_modules/y.js": "ignored",
        ".env.local": "sensitive",
    }


def test_working_tree_diff(obs: GitObserver, git_repo: Path) -> None:
    write(git_repo, "README.md", "edited\n")
    result = obs.diff("HEAD", None, ignore_globs=IGNORE, max_diff_chars=4000)
    assert [f.path for f in result.files] == ["README.md"]
    assert obs.file_at(None, "README.md") == "edited\n"
    assert obs.file_at("HEAD", "README.md") == "hello\n"
    assert obs.file_at("HEAD", "missing.txt") is None


def test_snippet_truncated_with_hunk_headers(obs: GitObserver, git_repo: Path) -> None:
    lines = [f"value_{i} = {i}\n" for i in range(400)]
    write(git_repo, "big.py", "".join(lines))
    commit_all(git_repo)
    lines[5] = "value_5 = 'changed'\n"
    lines[200] = "value_200 = 'changed'\n"
    lines[395] = "value_395 = 'changed'\n"
    write(git_repo, "big.py", "".join(lines))
    sha = commit_all(git_repo)
    [change] = _diff(obs, sha, max_chars=150).files
    headers = [ln for ln in change.patch.splitlines() if ln.startswith("@@")]
    assert len(headers) == 3
    for header in headers:
        assert header in change.snippet
    assert "truncated" in change.snippet
    assert len(change.snippet) < len(change.patch)


# ----- pure helpers ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("uv.lock", True),
        ("sub/poetry.lock", True),
        ("node_modules/a/b.js", True),
        ("packages/web/node_modules/a.js", True),
        ("dist/app.js", True),
        ("src/app.min.js", True),
        ("src/app.js", False),
        ("distribution/app.js", False),
        ("lockfile.py", False),
    ],
)
def test_matches_ignore(path: str, expected: bool) -> None:
    assert matches_ignore(path, IGNORE) is expected


def test_truncate_short_diff_unchanged() -> None:
    diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b"
    assert truncate_diff(diff, 1000) == diff


# ----- hooks --------------------------------------------------------------------------


def _hook(git_repo: Path) -> Path:
    return git_repo / ".git" / "hooks" / "post-commit"


def test_install_creates_executable_hook(obs: GitObserver, git_repo: Path) -> None:
    result = obs.install_hook()
    assert result.action == HookAction.created
    content = _hook(git_repo).read_text()
    assert content.startswith("#!/bin/sh\n")
    assert HOOK_START in content and HOOK_END in content
    assert "-m contextbridge.main hook post-commit" in content
    assert ") >/dev/null 2>&1 &" in content  # backgrounded, output discarded
    assert _hook(git_repo).stat().st_mode & stat.S_IXUSR


def test_install_is_idempotent(obs: GitObserver, git_repo: Path) -> None:
    obs.install_hook()
    before = _hook(git_repo).read_text()
    assert obs.install_hook().action == HookAction.already_installed
    assert _hook(git_repo).read_text() == before
    assert before.count(HOOK_START) == 1


def test_install_appends_to_existing_hook(obs: GitObserver, git_repo: Path) -> None:
    existing = "#!/bin/bash\necho 'user hook ran'\n"
    _hook(git_repo).write_text(existing)
    _hook(git_repo).chmod(0o755)
    assert obs.install_hook().action == HookAction.appended
    content = _hook(git_repo).read_text()
    assert content.startswith(existing)
    assert content.index("user hook ran") < content.index(HOOK_START)

    assert obs.uninstall_hook().action == HookAction.removed
    assert _hook(git_repo).read_text() == existing


def test_install_inserts_before_trailing_exit(obs: GitObserver, git_repo: Path) -> None:
    _hook(git_repo).write_text("#!/bin/sh\necho hi\nexit 0\n")
    _hook(git_repo).chmod(0o755)
    obs.install_hook()
    content = _hook(git_repo).read_text()
    assert content.index(HOOK_END) < content.index("exit 0")


def test_install_refuses_non_shell_hook(obs: GitObserver, git_repo: Path) -> None:
    original = "#!/usr/bin/env python3\nprint('hi')\n"
    _hook(git_repo).write_text(original)
    assert obs.install_hook().action == HookAction.unsupported_shell
    assert _hook(git_repo).read_text() == original


def test_uninstall_removes_file_we_created(obs: GitObserver, git_repo: Path) -> None:
    assert obs.uninstall_hook().action == HookAction.not_installed
    obs.install_hook()
    assert obs.uninstall_hook().action == HookAction.removed_file
    assert not _hook(git_repo).exists()


def test_respects_core_hooks_path(obs: GitObserver, git_repo: Path) -> None:
    git(git_repo, "config", "core.hooksPath", ".githooks")
    result = obs.install_hook()
    assert result.path == (git_repo / ".githooks" / "post-commit").resolve()
    assert result.path.is_file()
    assert not _hook(git_repo).exists()


def test_installed_hook_does_not_block_commit(git_repo: Path, obs: GitObserver) -> None:
    """Even when `cb` cannot do anything useful, the commit succeeds quickly."""
    obs.install_hook()
    write(git_repo, "a.txt", "x")
    git(git_repo, "add", "a.txt")
    git(git_repo, "commit", "-q", "-m", "with hook")  # check=True: must exit 0


def test_install_leaves_disabled_hook_untouched(obs: GitObserver, git_repo: Path) -> None:
    """A non-executable hook is disabled; enabling it would run the user's old code."""
    original = "#!/bin/sh\necho 'switched off on purpose'\n"
    _hook(git_repo).write_text(original)
    _hook(git_repo).chmod(0o644)
    assert obs.install_hook().action == HookAction.disabled_hook
    assert _hook(git_repo).read_text() == original
    assert not _hook(git_repo).stat().st_mode & stat.S_IXUSR


def test_refresh_keeps_user_permissions(obs: GitObserver, git_repo: Path) -> None:
    obs.install_hook()
    content = _hook(git_repo).read_text()
    _hook(git_repo).write_text(content.replace("Installed by", "Old block, installed by"))
    _hook(git_repo).chmod(0o644)  # user disabled the hook
    assert obs.install_hook().action == HookAction.already_installed
    assert not _hook(git_repo).stat().st_mode & stat.S_IXUSR


def test_hook_prefers_installing_interpreter_over_cb_on_path(
    git_repo: Path, tmp_path: Path
) -> None:
    """An unrelated `cb` on PATH must not replace the interpreter that ran `cb init`."""
    obs = GitObserver.open(git_repo)
    obs.install_hook()
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    marker = tmp_path / "wrong-cb-ran"
    fake = fake_bin / "cb"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    subprocess.run(["sh", str(_hook(git_repo))], cwd=git_repo, env=env, check=True)
    time.sleep(1.5)  # the hook body runs in the background
    assert not marker.exists()
