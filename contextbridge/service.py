"""Operations shared by every front end (the CLI and the desktop app).

Nothing here prints, prompts, or imports Typer/Qt. Methods return plain data and raise
:class:`~contextbridge.errors.ContextBridgeError` subclasses for expected problems, so a
front end only has to present results and messages.
"""

from __future__ import annotations

import codecs
import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextbridge.adapters.base_adapter import MAX_ACTIVE_FILES, BaseAdapter
from contextbridge.adapters.registry import all_adapters, get_adapter, resolve_name
from contextbridge.config import save_config
from contextbridge.core.git_observer import HOOK_START, GitObserver, HookResult
from contextbridge.core.sanitizer import SanitizeResult, sanitize, sanitize_with_report
from contextbridge.core.shell_listener import (
    CommandResult,
    TestReport,
    parse_output,
    run_command,
    unknown_output_failure,
)
from contextbridge.errors import (
    ContextBridgeError,
    NotAGitRepoError,
    NotInitializedError,
    UnknownAgentError,
    UserError,
)
from contextbridge.storage.db import (
    Decision,
    HandoffRecord,
    SearchHit,
    Session,
    TestRun,
    keyword_search,
    utc_now,
)
from contextbridge.storage.workspace import (
    Workspace,
    ensure_gitignored,
    find_context_dir,
    find_project_root,
)
from contextbridge.synthesis.compressor import Handoff, build_handoff, format_ratio
from contextbridge.synthesis.context_builder import SessionContext, build_context

log = logging.getLogger("contextbridge")

MAX_LABEL_CHARS = 40
VECTOR_DIRNAME = "vector_db"
OLLAMA_CHECK_TIMEOUT = 2.0


# ----- environment checks ----------------------------------------------------------------


def git_is_installed() -> bool:
    """True if a working ``git`` is on PATH."""
    try:
        proc = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and proc.stdout.startswith("git version")


def agent_label(name: str | None) -> str | None:
    """The agent being left. Free text (it may be an agent with no adapter), normalised."""
    if not name or not name.strip():
        return None
    try:
        return resolve_name(name)
    except UnknownAgentError:
        return sanitize(name.strip().lower())[:MAX_LABEL_CHARS]


def detect_test_command(root: Path) -> str | None:
    """A sensible default test command for the project, or None if we can't tell."""

    def has(*names: str) -> bool:
        return any((root / n).exists() for n in names)

    if has("package.json"):
        try:
            scripts = json.loads((root / "package.json").read_text(encoding="utf-8")).get("scripts")
        except (OSError, ValueError):
            scripts = None
        if isinstance(scripts, dict) and "test" in scripts:
            if has("pnpm-lock.yaml"):
                return "pnpm test"
            return "yarn test" if has("yarn.lock") else "npm test"
    if has("go.mod"):
        return "go test ./..."
    if has("Cargo.toml"):
        return "cargo test"
    if has("pytest.ini", "tox.ini", "conftest.py", "tests", "test") or (
        has("pyproject.toml") and not has("package.json")
    ):
        return "uv run pytest -q" if has("uv.lock") else "pytest -q"
    return None


# ----- result types ----------------------------------------------------------------------


@dataclass(frozen=True)
class InitResult:
    workspace: Workspace
    created_dir: bool
    gitignore_changed: bool
    migrations: int
    schema_version: int
    session: Session
    created_session: bool
    hook: HookResult | None  # None when skipped
    hook_skipped: str | None  # "no-hooks" | "not-a-git-repo"


@dataclass(frozen=True)
class Snapshot:
    """Everything a dashboard needs, gathered in one pass."""

    context: SessionContext
    last_handoff: dict[str, Any] | None


@dataclass(frozen=True)
class NoteResult:
    decision: Decision
    redactions: SanitizeResult


@dataclass(frozen=True)
class TestOutcome:
    __test__ = False  # Not a pytest test class.

    run: TestRun
    report: TestReport
    result: CommandResult


@dataclass
class PreparedHandoff:
    """A built handoff that has not been written yet. What you preview is what is written."""

    session_id: str
    context: SessionContext
    handoff: Handoff
    adapter: BaseAdapter | None
    from_label: str | None
    active_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommittedHandoff:
    record: HandoffRecord
    written: Path


@dataclass(frozen=True)
class AISettings:
    mode: str  # "off" | "local" | "cloud" (cloud is configured outside the app)
    model: str
    host: str


@dataclass(frozen=True)
class OllamaCheck:
    ok: bool
    message: str


# ----- streaming helper ----------------------------------------------------------------------


class _CallbackStream:
    """File-like sink handed to `run_command`; decodes bytes and forwards text."""

    def __init__(self, callback: Callable[[str], None]) -> None:
        self._callback = callback
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._lock = threading.Lock()

    @property
    def buffer(self) -> _CallbackStream:
        return self

    def write(self, data: bytes) -> int:
        with self._lock:
            text = self._decoder.decode(data)
        if text:
            self._callback(text)
        return len(data)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


def _shell_command(command: str) -> list[str]:
    """Run ``command`` through a login shell so the user's PATH (npm, pytest...) applies.

    A desktop app launched from Finder or the Start menu has a minimal PATH.
    """
    if os.name == "nt":
        return ["cmd", "/c", command]
    shell = os.environ.get("SHELL") or ("/bin/zsh" if Path("/bin/zsh").exists() else "/bin/sh")
    return [shell, "-l", "-c", command]


# ----- the service ---------------------------------------------------------------------------


class ProjectService:
    """Everything that can be done to one project folder."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.ws = Workspace(root=self.root)

    @classmethod
    def for_path(cls, path: Path) -> ProjectService:
        """The service for the project containing ``path`` (its git root, or ``path``)."""
        try:
            return cls(find_context_dir(path).parent)
        except NotInitializedError:
            return cls(find_project_root(path))

    # ----- state -------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.root.name

    def is_initialized(self) -> bool:
        return (self.ws.context_dir / "state.db").is_file()

    def is_git_repo(self) -> bool:
        return self._observer() is not None

    def _require_init(self) -> Workspace:
        if not self.is_initialized():
            raise NotInitializedError(str(self.root))
        self.ws.setup_logging()
        return self.ws

    def _observer(self) -> GitObserver | None:
        try:
            return GitObserver.open(self.root)
        except ContextBridgeError:  # not a repo, or git missing
            return None

    # ----- setup -------------------------------------------------------------------------

    def init(self, *, install_hook: bool = True, verbose: bool = False) -> InitResult:
        """Create `.context/`, the database and a session; optionally install the hook."""
        ws, created_dir = Workspace.create(self.root)
        ws.setup_logging(verbose=verbose)
        ws.load_config()  # Fail early on an invalid config file.
        gitignore_changed = ensure_gitignored(self.root)
        with ws.open_db(migrate=False) as db:
            migrations = db.migrate()
            schema_version = db.schema_version
            state = ws.load_state()
            session, created_session = ws.resolve_active_session(db, state)
            ws.save_state(state)
        log.info("init: root=%s session=%s", self.root, session.id)

        hook: HookResult | None = None
        skipped: str | None = None
        if not install_hook:
            skipped = "no-hooks"
        else:
            observer = self._observer()
            if observer is None:
                skipped = "not-a-git-repo"
            else:
                hook = observer.install_hook()
                log.info("init: hook %s at %s", hook.action.value, hook.path)
        return InitResult(
            ws, created_dir, gitignore_changed, migrations, schema_version,
            session, created_session, hook, skipped,
        )  # fmt: skip

    def hook_installed(self) -> bool:
        observer = self._observer()
        if observer is None:
            return False
        path = observer.hook_path()
        try:
            return path.is_file() and HOOK_START in path.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            return False

    def install_hook(self) -> HookResult:
        observer = self._observer()
        if observer is None:
            raise NotAGitRepoError(str(self.root))
        return observer.install_hook()

    def uninstall_hook(self) -> HookResult:
        observer = self._observer()
        if observer is None:
            raise NotAGitRepoError(str(self.root))
        return observer.uninstall_hook()

    # ----- reading -----------------------------------------------------------------------

    def snapshot(self, *, include_worktree: bool = True) -> Snapshot:
        ws = self._require_init()
        config = ws.load_config()
        with ws.open_db() as db:
            session, _ = ws.active_session(db)
            context = build_context(
                db,
                session,
                config,
                project=ws.load_state().project_name,
                observer=self._observer(),
                include_worktree=include_worktree,
            )
        return Snapshot(context=context, last_handoff=ws.load_state().last_handoff)

    def search(self, query: str, *, limit: int = 20) -> list[SearchHit]:
        ws = self._require_init()
        query = sanitize(query).strip()
        if not query:
            raise UserError("Search query is empty.")
        with ws.open_db() as db:
            return keyword_search(db, query, limit=limit)

    # ----- writing notes and test runs ---------------------------------------------------

    def add_note(self, text: str, tag: str = "note") -> NoteResult:
        if not text.strip():
            raise UserError("Write something first.")
        ws = self._require_init()
        report = sanitize_with_report(text)
        with ws.open_db() as db:
            session, _ = ws.active_session(db)
            decision = db.add_decision(session.id, report.text, tag)
        log.info("note: stored %s tag=%s redactions=%d", decision.id, tag, report.total)
        return NoteResult(decision, report)

    def record_test_run(self, command: str, result: CommandResult, report: TestReport) -> TestRun:
        """Store a finished command run (used by both `cb run` and the app)."""
        ws = self._require_init()
        failures = report.failures if report.recognized else unknown_output_failure(result)
        with ws.open_db() as db:
            session, _ = ws.active_session(db)
            run = db.add_test_run(
                session.id,
                command,
                result.exit_code,
                passed=report.passed,
                failed=report.failed,
                skipped=report.skipped,
                failures=failures,
            )
        log.info(
            "run: %s exit=%d runner=%s passed=%s failed=%s",
            run.id, result.exit_code, report.runner, report.passed, report.failed,
        )  # fmt: skip
        return run

    def run_tests(
        self, command: str, *, on_output: Callable[[str], None] | None = None
    ) -> TestOutcome:
        """Run a command line (through a login shell), stream its output, record the result."""
        if not command.strip():
            raise UserError("Type a command to run, for example: pytest")
        self._require_init()
        sink = _CallbackStream(on_output or (lambda _text: None))
        result = run_command(_shell_command(command), cwd=self.root, stdout=sink, stderr=sink)
        try:
            hint = shlex.split(command, posix=os.name != "nt")
        except ValueError:
            hint = command.split()
        report = parse_output(hint, result.output)
        run = self.record_test_run(command.strip(), result, report)
        return TestOutcome(run=run, report=report, result=result)

    # ----- handoff -----------------------------------------------------------------------

    def prepare_handoff(
        self,
        from_agent: str | None,
        to_agent: str | None,
        *,
        use_llm: bool = True,
        force: bool = False,
        notify: Callable[[str], None] | None = None,
    ) -> PreparedHandoff:
        """Build the handoff (validating the target first) without writing anything."""
        ws = self._require_init()
        config = ws.load_config()
        # Validate the target before doing any work, so a typo fails instantly.
        adapter = get_adapter(to_agent, ws.root, force=force) if to_agent else None
        label = agent_label(from_agent)
        with ws.open_db() as db:
            session, _ = ws.active_session(db)
            context = build_context(
                db,
                session,
                config,
                project=ws.load_state().project_name,
                observer=self._observer(),
            )
        handoff = build_handoff(
            context,
            config,
            from_agent=label,
            to_agent=adapter.name if adapter else None,
            no_llm=not use_llm,
            notify=notify,
        )
        return PreparedHandoff(
            session_id=session.id,
            context=context,
            handoff=handoff,
            adapter=adapter,
            from_label=label,
            active_files=[f.path for f in context.files[:MAX_ACTIVE_FILES]],
        )

    def commit_handoff(self, prepared: PreparedHandoff) -> CommittedHandoff:
        """Write a prepared handoff into the agent's file and record it."""
        adapter, label, handoff = prepared.adapter, prepared.from_label, prepared.handoff
        if adapter is None or label is None:
            raise UserError("A handoff needs both a source and a target agent.")
        ws = self._require_init()
        written = adapter.inject_payload(handoff.payload, prepared.active_files)
        ratio = format_ratio(handoff.compression_ratio)
        with ws.open_db() as db:
            record = db.add_handoff(
                prepared.session_id,
                label,
                adapter.name,
                raw_tokens=handoff.raw_tokens,
                compressed_tokens=handoff.compressed_tokens,
                method=handoff.method,
                payload=handoff.payload,
                written_path=adapter.relative_path,
            )
            state = ws.load_state()
            state.last_handoff = {
                "timestamp": utc_now(),
                "from": label,
                "to": adapter.name,
                "raw_tokens": handoff.raw_tokens,
                "compressed_tokens": handoff.compressed_tokens,
                "compression_ratio": ratio,
                "method": handoff.method,
            }
            ws.save_state(state)
        log.info(
            "handoff: %s %s -> %s wrote %s method=%s raw=%d compressed=%d",
            record.id, label, adapter.name, adapter.relative_path,
            handoff.method, handoff.raw_tokens, handoff.compressed_tokens,
        )  # fmt: skip
        return CommittedHandoff(record=record, written=written)

    # ----- clearing ----------------------------------------------------------------------

    def archive_session(self) -> Session | None:
        """Archive the active session. Returns it, or None if there was none."""
        ws = self._require_init()
        with ws.open_db() as db:
            state = ws.load_state()
            session = db.get_session(state.active_session_id) if state.active_session_id else None
            if session is None or session.status != "active":
                return None
            db.set_session_status(session.id, "archived")
            state.active_session_id = None
            state.active_branch = None
            ws.save_state(state)
        log.info("clear: archived %s", session.id)
        return session

    def count_all(self) -> dict[str, int]:
        ws = self._require_init()
        with ws.open_db() as db:
            return db.count_all()

    def has_vector_index(self) -> bool:
        return (self.ws.context_dir / VECTOR_DIRNAME).exists()

    def delete_all(self) -> dict[str, int]:
        """Permanently delete every session and its data. Config and agent files stay."""
        ws = self._require_init()
        with ws.open_db() as db:
            deleted = db.delete_all_sessions()
        state = ws.load_state()
        state.active_session_id = None
        state.active_branch = None
        state.last_handoff = None
        ws.save_state(state)
        shutil.rmtree(ws.context_dir / VECTOR_DIRNAME, ignore_errors=True)
        log.info("clear: deleted all session data")
        return deleted

    def cleanup_adapters(self) -> list[str]:
        """Remove everything ContextBridge injected into agent files. Returns the paths."""
        ws = self._require_init()
        removed = []
        for adapter in all_adapters(ws.root):
            if adapter.is_installed():
                adapter.cleanup()
                removed.append(adapter.relative_path)
        log.info("clear: adapter cleanup removed %s", removed)
        return removed

    # ----- AI settings ---------------------------------------------------------------------

    def load_ai_settings(self) -> AISettings:
        ws = self._require_init()
        cfg = ws.load_config().llm
        mode = {"none": "off", "ollama": "local"}.get(cfg.provider, "cloud")
        return AISettings(mode=mode, model=cfg.model, host=cfg.ollama_host)

    def save_ai_settings(self, mode: str, model: str | None = None) -> None:
        """Save the AI choice ("off" or "local") in this project's config."""
        if mode not in ("off", "local"):
            raise UserError(f"Unknown AI mode {mode!r}.")
        ws = self._require_init()
        try:
            data = json.loads(ws.config_path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        existing = data.get("llm")
        llm: dict[str, Any] = existing if isinstance(existing, dict) else {}
        llm["provider"] = "ollama" if mode == "local" else "none"
        if model and model.strip():
            llm["model"] = model.strip()
        data["llm"] = llm
        save_config(data, ws.config_path)
        ws.load_config()  # Validate what we just wrote.


def check_ollama(host: str, model: str) -> OllamaCheck:
    """Is Ollama running, and does it have ``model``? Never raises."""
    try:
        import ollama

        listing = ollama.Client(host=host, timeout=OLLAMA_CHECK_TIMEOUT).list()
        names = {m.model for m in listing.models if m.model}
    except ConnectionError:
        return OllamaCheck(False, "Ollama isn't running. Open the Ollama app, then try again.")
    except Exception as exc:
        return OllamaCheck(False, f"Couldn't talk to Ollama ({type(exc).__name__}).")
    wanted = {model, f"{model}:latest"}
    if names & wanted:
        return OllamaCheck(True, f"Ollama is running and {model} is ready.")
    return OllamaCheck(
        False,
        f"Ollama is running, but {model} isn't installed yet. Install it with: ollama pull {model}",
    )


__all__ = [
    "AISettings",
    "CommittedHandoff",
    "InitResult",
    "NoteResult",
    "OllamaCheck",
    "PreparedHandoff",
    "ProjectService",
    "Snapshot",
    "TestOutcome",
    "agent_label",
    "check_ollama",
    "detect_test_command",
    "git_is_installed",
]
