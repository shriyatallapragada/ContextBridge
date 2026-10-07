"""Git observer: read repository state and diffs via the `git` CLI, and manage hooks.

Every call runs ``git -C <repo_root> ...`` through :mod:`subprocess` (no GitPython).
Handles: not a repo, detached HEAD, no commits yet, and merge/rebase in progress.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import Path

from contextbridge.core.sanitizer import is_sensitive_file
from contextbridge.errors import EnvironmentProblemError, NotAGitRepoError

GIT_TIMEOUT_SECONDS = 15
# Full patches above this size are not parsed for symbols (the snippet is still kept).
MAX_PATCH_CHARS = 512_000
# Files read for AST parsing above this size are ignored.
MAX_SOURCE_BYTES = 1_000_000

HOOK_NAME = "post-commit"
HOOK_START = "# >>> contextbridge >>>"
HOOK_END = "# <<< contextbridge <<<"
_SHELLS = ("sh", "bash", "zsh", "dash", "ksh")

# Environment variables that pin git to a location; drop them so `-C` is authoritative
# (git sets some of these while running hooks).
_LOCATION_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR")


class GitError(EnvironmentProblemError):
    """A git command failed unexpectedly."""


class ChangeType(StrEnum):
    modified = "modified"
    added = "added"
    deleted = "deleted"
    renamed = "renamed"


@dataclass(frozen=True)
class RepoState:
    branch: str | None  # None when HEAD is detached
    head_sha: str | None  # None when there are no commits yet
    operation: str | None  # "merge" | "rebase" | "cherry-pick" | "revert" | "bisect"

    @property
    def detached(self) -> bool:
        return self.branch is None and self.head_sha is not None

    @property
    def has_commits(self) -> bool:
        return self.head_sha is not None


@dataclass(frozen=True)
class WorkingTreeStatus:
    staged: list[str] = field(default_factory=list)
    unstaged: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.staged or self.unstaged or self.untracked)


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    committed_at: str  # ISO-8601 with offset, as git prints it
    subject: str


@dataclass(frozen=True)
class FileDiff:
    path: str
    change_type: ChangeType
    old_path: str | None = None  # Set for renames.
    patch: str = ""  # Full unified diff (capped at MAX_PATCH_CHARS).
    snippet: str = ""  # Truncated to max_diff_chars_per_file, hunk headers kept.


@dataclass(frozen=True)
class SkippedFile:
    path: str
    reason: str  # "ignored" | "sensitive" | "binary"


@dataclass
class DiffResult:
    files: list[FileDiff] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)


class HookAction(StrEnum):
    created = "created"
    appended = "appended"
    already_installed = "already_installed"
    unsupported_shell = "unsupported_shell"
    disabled_hook = "disabled_hook"  # Existing hook is not executable; left untouched.
    removed = "removed"
    removed_file = "removed_file"
    not_installed = "not_installed"


@dataclass(frozen=True)
class HookResult:
    action: HookAction
    path: Path


# ----- pure helpers ----------------------------------------------------------


def matches_ignore(path: str, globs: list[str]) -> bool:
    """True if ``path`` (repo-relative, ``/``-separated) matches any ignore glob.

    Patterns match the full path or the basename; ``dir/**`` also matches ``dir/``
    at any depth (e.g. ``node_modules/**`` matches ``web/node_modules/x.js``).
    """
    name = path.rsplit("/", 1)[-1]
    for pattern in globs:
        if fnmatch(path, pattern) or fnmatch(name, pattern):
            return True
        if pattern.endswith("/**"):
            prefix = pattern[:-3].strip("/")
            if prefix and f"/{prefix}/" in f"/{path}":
                return True
    return False


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def truncate_diff(diff: str, max_chars: int) -> str:
    """Truncate a unified diff to roughly ``max_chars``, always keeping hunk headers.

    File headers and every ``@@`` line are kept; hunk bodies are filled in order until
    the budget is spent. Dropped body lines are summarized with a marker line.
    """
    if len(diff) <= max_chars:
        return diff
    lines = diff.splitlines()
    first_hunk = next((i for i, ln in enumerate(lines) if _HUNK_HEADER.match(ln)), len(lines))
    header = lines[:first_hunk]
    # Group body lines under their hunk header.
    hunks: list[tuple[str, list[str]]] = []
    for line in lines[first_hunk:]:
        if _HUNK_HEADER.match(line):
            hunks.append((line, []))
        elif hunks:
            hunks[-1][1].append(line)

    fixed = sum(len(ln) + 1 for ln in header) + sum(len(h) + 1 for h, _ in hunks)
    budget = max(max_chars - fixed, 0)
    out = list(header)
    dropped = 0
    for hunk_header, body in hunks:
        out.append(hunk_header)
        for i, line in enumerate(body):
            if len(line) + 1 > budget:
                dropped += len(body) - i
                break
            out.append(line)
            budget -= len(line) + 1
    if dropped:
        out.append(f"[... truncated {dropped} line(s) ...]")
    text = "\n".join(out)
    # Pathological case: headers alone exceed the budget.
    if len(text) > max_chars * 2:
        text = text[: max_chars * 2] + "\n[... truncated ...]"
    return text


# ----- hook script -------------------------------------------------------------


def hook_block() -> str:
    """The shell snippet placed between the marker lines.

    Runs in the background, discards output and never fails the commit. Uses the
    interpreter that ran `cb init` first, so an unrelated or older `cb` on PATH cannot
    take over; falls back to `cb` on PATH if that interpreter is gone.
    """
    python = shlex.quote(sys.executable)
    return "\n".join(
        [
            HOOK_START,
            "# Installed by `cb init`; remove with `cb init --uninstall-hooks`.",
            "(",
            f"  if [ -x {python} ] && {python} -m contextbridge.main hook post-commit; then :",
            "  elif command -v cb >/dev/null 2>&1; then cb hook post-commit; fi",
            ") >/dev/null 2>&1 &",
            HOOK_END,
        ]
    )


def _strip_block(content: str) -> tuple[str, bool]:
    """Remove every ContextBridge block from hook ``content``."""
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    inside = found = False
    for line in lines:
        stripped = line.strip()
        if stripped == HOOK_START:
            inside = found = True
            continue
        if inside:
            if stripped == HOOK_END:
                inside = False
            continue
        out.append(line)
    return "".join(out), found


def _shebang_shell(content: str) -> str | None:
    first = content.splitlines()[0] if content else ""
    if not first.startswith("#!"):
        return None
    parts = first[2:].strip().split()
    if not parts:
        return None
    prog = Path(parts[0]).name
    if prog == "env" and len(parts) > 1:
        prog = Path(parts[1]).name
    return prog


def _insert_block(content: str, block: str) -> str:
    """Append ``block``, but before a trailing top-level ``exit`` so it still runs."""
    lines = content.rstrip("\n").splitlines()
    idx = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^exit(\s|$)", stripped):
            idx = i
        break
    new_lines = [*lines[:idx], "", block, *lines[idx:]]
    return "\n".join(new_lines) + "\n"


# ----- observer -----------------------------------------------------------------


class GitObserver:
    """Read-only view of a git repository (plus hook management)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def open(cls, path: Path) -> GitObserver:
        """Locate the work tree containing ``path``; raise NotAGitRepoError otherwise."""
        try:
            proc = _run_git(path, "rev-parse", "--show-toplevel")
        except FileNotFoundError as exc:
            raise EnvironmentProblemError(
                "git is not installed or not on PATH.", hint="Install git and retry."
            ) from exc
        if proc.returncode != 0 or not proc.stdout.strip():
            raise NotAGitRepoError(str(path))
        return cls(Path(proc.stdout.strip()).resolve())

    # ----- plumbing -------------------------------------------------------------

    def git(self, *args: str, check: bool = True) -> str:
        """Run git in this repo and return stdout (invalid UTF-8 is replaced)."""
        proc = _run_git(self.root, *args)
        if check and proc.returncode != 0:
            cmd = " ".join(args[:3])
            raise GitError(f"`git {cmd}` failed: {proc.stderr.strip()[:300]}")
        return proc.stdout

    def _try(self, *args: str) -> str | None:
        proc = _run_git(self.root, *args)
        return proc.stdout if proc.returncode == 0 else None

    def git_path(self, name: str) -> Path:
        """Resolve ``git rev-parse --git-path`` (honours worktrees and core.hooksPath)."""
        out = self.git("rev-parse", "--git-path", name).strip()
        p = Path(out)
        return p if p.is_absolute() else (self.root / p).resolve()

    # ----- state ----------------------------------------------------------------

    def repo_state(self) -> RepoState:
        head = self._try("rev-parse", "--verify", "--quiet", "HEAD^{commit}")
        branch = self._try("symbolic-ref", "--quiet", "--short", "HEAD")
        head_sha = head.strip() if head else None
        branch_name = branch.strip() if branch else None
        return RepoState(branch=branch_name, head_sha=head_sha, operation=self._operation())

    def _operation(self) -> str | None:
        checks = (
            ("rebase", ("rebase-merge", "rebase-apply")),
            ("merge", ("MERGE_HEAD",)),
            ("cherry-pick", ("CHERRY_PICK_HEAD",)),
            ("revert", ("REVERT_HEAD",)),
            ("bisect", ("BISECT_LOG",)),
        )
        git_dir = Path(self.git("rev-parse", "--absolute-git-dir").strip())
        for op, names in checks:
            if any((git_dir / n).exists() for n in names):
                return op
        return None

    def current_branch(self) -> str | None:
        return self.repo_state().branch

    def status(self) -> WorkingTreeStatus:
        """Staged, unstaged and untracked paths (porcelain v1, NUL-separated)."""
        out = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        status = WorkingTreeStatus()
        entries = out.split("\0")
        i = 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            if len(entry) < 4:
                continue
            x, y, path = entry[0], entry[1], entry[3:]
            if x in "RC":
                i += 1  # The next field is the rename/copy source.
            if x == "?" and y == "?":
                status.untracked.append(path)
                continue
            if x not in " ?!":
                status.staged.append(path)
            if y not in " ?!":
                status.unstaged.append(path)
        return status

    def commit_infos(self, shas: list[str]) -> list[CommitInfo]:
        """Info for specific commits, newest first; unknown SHAs are skipped."""
        known = [
            sha for sha in shas if self._try("cat-file", "-e", f"{sha}^{{commit}}") is not None
        ]
        if not known:
            return []
        out = self.git("log", "--no-walk=sorted", "--format=%H%x1f%cI%x1f%s%x1e", *known, "--")
        commits = []
        for record in out.split("\x1e"):
            parts = record.strip("\n").split("\x1f")
            if len(parts) == 3:
                commits.append(CommitInfo(sha=parts[0], committed_at=parts[1], subject=parts[2]))
        return commits

    def commits_since(
        self, since_iso: str, *, exclude: str | None = None, limit: int = 100
    ) -> list[CommitInfo]:
        """Commits reachable from HEAD committed at/after ``since_iso`` (newest first).

        With ``exclude``, commits reachable from that revision are left out
        (``exclude..HEAD``); an unknown ``exclude`` (e.g. rewritten by a rebase) is ignored.
        """
        if not self.repo_state().has_commits:
            return []
        revs = ["HEAD"]
        if exclude and self._try("rev-parse", "--verify", "--quiet", f"{exclude}^{{commit}}"):
            revs.append(f"^{exclude}")
        out = self.git(
            "log",
            f"--since={since_iso}",
            f"--max-count={limit}",
            "--format=%H%x1f%cI%x1f%s%x1e",
            *revs,
            "--",
        )
        since = _parse_iso(since_iso)
        commits = []
        for record in out.split("\x1e"):
            parts = record.strip("\n").split("\x1f")
            # git's date parsing is approximate; filter exactly as well.
            if len(parts) != 3:
                continue
            committed = _parse_iso(parts[1])
            if since is None or committed is None or committed >= since:
                commits.append(CommitInfo(sha=parts[0], committed_at=parts[1], subject=parts[2]))
        return commits

    # ----- diffs ----------------------------------------------------------------

    def empty_tree(self) -> str:
        return self.git("hash-object", "-t", "tree", "--stdin").strip() or (
            "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        )

    def parent_of(self, sha: str) -> str:
        """First parent of ``sha``, or the empty tree for a root commit."""
        parent = self._try("rev-parse", "--verify", "--quiet", f"{sha}^1")
        return parent.strip() if parent else self.empty_tree()

    def commit_changes(
        self,
        sha: str,
        *,
        ignore_globs: list[str],
        max_diff_chars: int,
        max_files: int = 200,
    ) -> DiffResult:
        """Per-file diffs introduced by ``sha`` (vs its first parent), renames detected."""
        return self.diff(
            self.parent_of(sha),
            sha,
            ignore_globs=ignore_globs,
            max_diff_chars=max_diff_chars,
            max_files=max_files,
        )

    def diff(
        self,
        base: str,
        target: str | None,
        *,
        ignore_globs: list[str],
        max_diff_chars: int,
        max_files: int = 200,
    ) -> DiffResult:
        """Per-file diffs from ``base`` to ``target`` (``None`` = the working tree)."""
        revs = [base] if target is None else [base, target]
        out = self.git("diff", "--no-color", "--no-ext-diff", "-M", "--name-status", "-z", *revs)
        result = DiffResult()
        fields = out.split("\0")
        i = 0
        while i < len(fields):
            code = fields[i]
            i += 1
            if not code:
                continue
            letter = code[0]
            if letter in "RC":
                old_path, path = fields[i], fields[i + 1]
                i += 2
            else:
                old_path, path = None, fields[i]
                i += 1
            change = {
                "A": ChangeType.added,
                "D": ChangeType.deleted,
                "R": ChangeType.renamed,
                "C": ChangeType.added,
            }.get(letter, ChangeType.modified)
            if letter == "C":
                old_path = None

            if is_sensitive_file(path) or (old_path and is_sensitive_file(old_path)):
                result.skipped.append(SkippedFile(path, "sensitive"))
                continue
            if matches_ignore(path, ignore_globs):
                result.skipped.append(SkippedFile(path, "ignored"))
                continue
            if len(result.files) >= max_files:
                result.skipped.append(SkippedFile(path, "limit"))
                continue

            pathspec = [old_path, path] if old_path else [path]
            patch = self.git("diff", "--no-color", "--no-ext-diff", "-M", *revs, "--", *pathspec)
            if _is_binary_patch(patch):
                result.skipped.append(SkippedFile(path, "binary"))
                continue
            result.files.append(
                FileDiff(
                    path=path,
                    change_type=change,
                    old_path=old_path,
                    patch=patch[:MAX_PATCH_CHARS],
                    snippet=truncate_diff(patch, max_diff_chars),
                )
            )
        return result

    def untracked_changes(self, *, ignore_globs: list[str], max_diff_chars: int) -> DiffResult:
        """New files not yet added to git (honouring .gitignore), as ``added`` diffs."""
        result = DiffResult()
        for path in self.status().untracked:
            if is_sensitive_file(path):
                result.skipped.append(SkippedFile(path, "sensitive"))
                continue
            if matches_ignore(path, ignore_globs):
                result.skipped.append(SkippedFile(path, "ignored"))
                continue
            target = self.root / path
            try:
                if target.stat().st_size > MAX_SOURCE_BYTES:
                    result.skipped.append(SkippedFile(path, "limit"))
                    continue
                data = target.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:8192]:
                result.skipped.append(SkippedFile(path, "binary"))
                continue
            lines = data.decode("utf-8", errors="replace").splitlines()
            patch = "\n".join(
                [
                    f"diff --git a/{path} b/{path}",
                    "new file (untracked)",
                    "--- /dev/null",
                    f"+++ b/{path}",
                    f"@@ -0,0 +1,{len(lines)} @@",
                    *(f"+{line}" for line in lines),
                ]
            )
            result.files.append(
                FileDiff(
                    path=path,
                    change_type=ChangeType.added,
                    patch=patch[:MAX_PATCH_CHARS],
                    snippet=truncate_diff(patch, max_diff_chars),
                )
            )
        return result

    def file_at(self, rev: str | None, path: str) -> str | None:
        """File contents at ``rev`` (``None`` = working tree), or None if absent/too big."""
        if rev is None:
            target = self.root / path
            try:
                if target.stat().st_size > MAX_SOURCE_BYTES:
                    return None
                return target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
        proc = _run_git(self.root, "show", f"{rev}:{path}")
        if proc.returncode != 0 or len(proc.stdout) > MAX_SOURCE_BYTES:
            return None
        return proc.stdout

    # ----- hooks ----------------------------------------------------------------

    def hooks_dir(self) -> Path:
        """The hooks directory, respecting ``core.hooksPath``."""
        return self.git_path("hooks")

    def hook_path(self, name: str = HOOK_NAME) -> Path:
        return self.hooks_dir() / name

    def install_hook(self) -> HookResult:
        """Add the ContextBridge block to the post-commit hook. Never overwrites."""
        path = self.hook_path()
        block = hook_block()
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace")
            stripped, found = _strip_block(content)
            shell = _shebang_shell(content)
            if shell is not None and shell not in _SHELLS:
                return HookResult(HookAction.unsupported_shell, path)
            if found:
                new_content = _insert_block(stripped, block)
                if new_content == content:
                    return HookResult(HookAction.already_installed, path)
                # Refresh an outdated block in place (e.g. a new interpreter path),
                # keeping the file's permissions as the user set them.
                path.write_text(new_content, encoding="utf-8")
                return HookResult(HookAction.already_installed, path)
            if not os.access(path, os.X_OK):
                # Git ignores non-executable hooks; making it executable would also
                # re-enable whatever the user deliberately switched off.
                return HookResult(HookAction.disabled_hook, path)
            path.write_text(_insert_block(content, block), encoding="utf-8")
            return HookResult(HookAction.appended, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\n\n{block}\n", encoding="utf-8")
        _make_executable(path)
        return HookResult(HookAction.created, path)

    def uninstall_hook(self) -> HookResult:
        """Remove only the ContextBridge block; delete the file if nothing else remains."""
        path = self.hook_path()
        if not path.exists():
            return HookResult(HookAction.not_installed, path)
        content = path.read_text(encoding="utf-8", errors="replace")
        stripped, found = _strip_block(content)
        if not found:
            return HookResult(HookAction.not_installed, path)
        meaningful = [ln for ln in stripped.splitlines() if ln.strip() and not ln.startswith("#!")]
        if not meaningful:
            path.unlink()
            return HookResult(HookAction.removed_file, path)
        path.write_text(re.sub(r"\n{3,}", "\n\n", stripped).rstrip("\n") + "\n", encoding="utf-8")
        return HookResult(HookAction.removed, path)


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _is_binary_patch(patch: str) -> bool:
    return any(
        line.startswith(("Binary files ", "GIT binary patch")) for line in patch.splitlines()[:8]
    )


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _LOCATION_ENV}
    env["LC_ALL"] = "C"
    env["GIT_OPTIONAL_LOCKS"] = "0"  # Never contend with the user's own git commands.
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), "-c", "core.quotePath=false", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env=_git_env(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"`git {' '.join(args[:2])}` timed out.") from exc
