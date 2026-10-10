"""The shared service layer: everything the desktop app does, without any GUI."""

from __future__ import annotations

import json
import shlex
import sqlite3
import sys
from pathlib import Path

import pytest

from contextbridge.core.git_observer import HOOK_START, HookAction
from contextbridge.errors import (
    AdapterConflictError,
    NotAGitRepoError,
    NotInitializedError,
    UnknownAgentError,
    UserError,
)
from contextbridge.service import (
    ProjectService,
    agent_label,
    check_ollama,
    detect_test_command,
    git_is_installed,
)
from tests.helpers import commit_all, write

PY = shlex.quote(sys.executable)


@pytest.fixture
def svc(git_repo: Path) -> ProjectService:
    write(git_repo, "cart.py", "def total(items):\n    return sum(items)\n")
    commit_all(git_repo, "add cart")
    service = ProjectService.for_path(git_repo)
    service.init(install_hook=False)
    commit_all(git_repo, "ignore .context/")
    return service


def _rows(root: Path, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(root / ".context" / "state.db")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ----- setup ---------------------------------------------------------------------------


def test_for_path_finds_git_root_and_existing_workspace(git_repo: Path) -> None:
    sub = git_repo / "a" / "b"
    sub.mkdir(parents=True)
    assert ProjectService.for_path(sub).root == git_repo.resolve()
    ProjectService.for_path(git_repo).init(install_hook=False)
    assert ProjectService.for_path(sub).is_initialized()


def test_init_is_idempotent_and_reports(git_repo: Path) -> None:
    service = ProjectService.for_path(git_repo)
    assert not service.is_initialized()
    first = service.init()
    assert first.created_dir and first.created_session and first.gitignore_changed
    assert first.hook is not None and first.hook.action == HookAction.created
    assert service.is_initialized() and service.hook_installed()
    second = service.init()
    assert not second.created_dir and not second.created_session
    assert second.hook is not None and second.hook.action == HookAction.already_installed
    assert second.session.id == first.session.id


def test_init_without_hook_and_outside_git(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    service = ProjectService.for_path(folder)
    assert not service.is_git_repo()
    result = service.init()
    assert result.hook is None and result.hook_skipped == "not-a-git-repo"
    assert not service.hook_installed()
    with pytest.raises(NotAGitRepoError):
        service.install_hook()
    assert service.add_note("works without git").decision.content == "works without git"


def test_hook_toggle(svc: ProjectService) -> None:
    assert not svc.hook_installed()
    assert svc.install_hook().action == HookAction.created
    assert svc.hook_installed()
    assert HOOK_START in (svc.root / ".git" / "hooks" / "post-commit").read_text()
    assert svc.uninstall_hook().action == HookAction.removed_file
    assert not svc.hook_installed()


def test_operations_require_init(git_repo: Path) -> None:
    service = ProjectService.for_path(git_repo)
    for call in (service.snapshot, service.count_all, service.archive_session):
        with pytest.raises(NotInitializedError):
            call()
    with pytest.raises(NotInitializedError):
        service.add_note("x")


# ----- notes and reading ---------------------------------------------------------------------


def test_add_note_sanitizes_and_validates(svc: ProjectService) -> None:
    result = svc.add_note("the key is AKIAIOSFODNN7EXAMPLE", "blocker")
    assert result.redactions.total == 1
    assert "AKIAIOSFODNN7EXAMPLE" not in result.decision.content
    assert result.decision.tag == "blocker"
    with pytest.raises(UserError, match="Write something"):
        svc.add_note("   ")
    with pytest.raises(UserError):
        svc.add_note("x", "not-a-tag")


def test_snapshot_includes_notes_and_live_changes(svc: ProjectService) -> None:
    svc.add_note("Use cents", "architecture")
    write(svc.root, "cart.py", "def total(items):\n    return sum(i.cents for i in items)\n")
    write(svc.root, "new_file.py", "x = 1\n")
    snap = svc.snapshot()
    assert [d.content for d in snap.context.decisions] == ["Use cents"]
    paths = {f.path: f for f in snap.context.files}
    assert paths["cart.py"].uncommitted and paths["new_file.py"].change_type == "added"
    assert snap.last_handoff is None
    assert [h.kind for h in svc.search("cents")] == ["decision"]
    with pytest.raises(UserError):
        svc.search("   ")


# ----- running tests ---------------------------------------------------------------------------


def test_run_tests_streams_output_and_records(svc: ProjectService) -> None:
    chunks: list[str] = []
    script = "import sys; print('hello'); print('oops', file=sys.stderr); sys.exit(3)"
    outcome = svc.run_tests(f"{PY} -c {shlex.quote(script)}", on_output=chunks.append)
    assert outcome.result.exit_code == 3
    streamed = "".join(chunks)
    assert "hello" in streamed and "oops" in streamed
    [row] = _rows(svc.root, "SELECT exit_code FROM test_runs")
    assert row == (3,)


def test_run_tests_parses_pytest_counts(svc: ProjectService) -> None:
    write(
        svc.root, "suite/test_x.py", "def test_a():\n    assert 1\n\ndef test_b():\n    assert 0\n"
    )
    outcome = svc.run_tests(f"{PY} -m pytest -q -p no:cacheprovider suite")
    assert (outcome.run.passed, outcome.run.failed) == (1, 1)
    assert outcome.run.failures[0]["test"] == "suite/test_x.py::test_b"


def test_run_tests_unknown_command_and_empty(svc: ProjectService) -> None:
    assert svc.run_tests("definitely-not-a-command-xyz").result.exit_code == 127
    with pytest.raises(UserError):
        svc.run_tests("  ")


def test_run_tests_decodes_split_utf8() -> None:
    from contextbridge.service import _CallbackStream

    got: list[str] = []
    stream = _CallbackStream(got.append)
    data = "naïve ✓".encode()
    stream.write(data[:3])  # splits the two-byte "ï"
    stream.write(data[3:])
    assert "".join(got) == "naïve ✓"


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"package.json": '{"scripts": {"test": "jest"}}'}, "npm test"),
        ({"package.json": '{"scripts": {"test": "jest"}}', "yarn.lock": ""}, "yarn test"),
        ({"package.json": '{"scripts": {"test": "jest"}}', "pnpm-lock.yaml": ""}, "pnpm test"),
        ({"package.json": '{"name": "x"}'}, None),
        ({"go.mod": "module x"}, "go test ./..."),
        ({"Cargo.toml": "[package]"}, "cargo test"),
        ({"pyproject.toml": "", "uv.lock": ""}, "uv run pytest -q"),
        ({"tests/test_a.py": ""}, "pytest -q"),
        ({"README.md": ""}, None),
    ],
)
def test_detect_test_command(tmp_path: Path, files: dict[str, str], expected: str | None) -> None:
    for name, content in files.items():
        write(tmp_path, name, content)
    assert detect_test_command(tmp_path) == expected


# ----- handoff: what you preview is what is written ---------------------------------------------


def test_prepare_then_commit_handoff(svc: ProjectService) -> None:
    svc.add_note("Prices are cents", "architecture")
    prepared = svc.prepare_handoff("claude", "cursor")
    assert prepared.adapter is not None and prepared.adapter.name == "cursor"
    assert not (svc.root / ".cursor").exists()  # preparing writes nothing
    assert "Prices are cents" in prepared.handoff.payload
    committed = svc.commit_handoff(prepared)
    written = committed.written.read_text()
    assert prepared.handoff.payload.strip() in written  # exactly what was previewed
    [(source, target, path)] = _rows(
        svc.root, "SELECT from_agent, to_agent, written_path FROM handoffs"
    )
    assert (source, target, path) == ("claude", "cursor", ".cursor/rules/handoff.mdc")
    last = svc.snapshot().last_handoff
    assert last is not None and last["to"] == "cursor"


def test_handoff_validation_and_conflict(svc: ProjectService) -> None:
    with pytest.raises(UnknownAgentError):
        svc.prepare_handoff("claude", "gemini")
    no_target = svc.prepare_handoff("claude", None)
    with pytest.raises(UserError):
        svc.commit_handoff(no_target)
    (svc.root / "HANDOFF.md").write_text("mine\n")
    prepared = svc.prepare_handoff("claude", "generic")
    with pytest.raises(AdapterConflictError):
        svc.commit_handoff(prepared)
    assert (svc.root / "HANDOFF.md").read_text() == "mine\n"
    assert _rows(svc.root, "SELECT * FROM handoffs") == []
    assert prepared.adapter is not None
    prepared.adapter.force = True
    svc.commit_handoff(prepared)
    assert "contextbridge:start" in (svc.root / "HANDOFF.md").read_text()


def test_agent_label_normalises() -> None:
    assert agent_label("Claude-Code") == "claude"
    assert agent_label("Gemini CLI") == "gemini cli"
    assert agent_label("  ") is None and agent_label(None) is None


# ----- clearing ----------------------------------------------------------------------------------


def test_archive_cleanup_and_delete(svc: ProjectService) -> None:
    svc.add_note("a note")
    svc.commit_handoff(svc.prepare_handoff("claude", "generic"))
    assert (svc.root / "HANDOFF.md").exists()
    assert svc.cleanup_adapters() == ["HANDOFF.md"]
    assert not (svc.root / "HANDOFF.md").exists()
    assert svc.cleanup_adapters() == []

    session = svc.archive_session()
    assert session is not None and svc.archive_session() is None
    svc.add_note("fresh note")  # starts a new session
    counts = svc.count_all()
    assert counts["sessions"] == 2 and counts["decision_logs"] == 2

    vector = svc.ws.context_dir / "vector_db"
    vector.mkdir()
    assert svc.has_vector_index()
    deleted = svc.delete_all()
    assert deleted["decision_logs"] == 2 and not vector.exists()
    assert set(svc.count_all().values()) == {0}
    assert svc.snapshot().last_handoff is None


# ----- AI settings ---------------------------------------------------------------------


def test_ai_settings_roundtrip(svc: ProjectService) -> None:
    # The default is "local": try Ollama, and fall back to the template.
    assert svc.load_ai_settings().mode == "local"
    svc.save_ai_settings("off")
    assert svc.load_ai_settings().mode == "off"
    cfg = json.loads(svc.ws.config_path.read_text())
    assert cfg["llm"]["provider"] == "none"
    svc.save_ai_settings("local", "llama3:8b")
    settings = svc.load_ai_settings()
    assert (settings.mode, settings.model) == ("local", "llama3:8b")
    with pytest.raises(UserError):
        svc.save_ai_settings("cloud")


def test_ai_settings_keep_other_config(svc: ProjectService) -> None:
    svc.ws.config_path.write_text(json.dumps({"capture": {"max_files_per_handoff": 7}}))
    svc.save_ai_settings("off")
    data = json.loads(svc.ws.config_path.read_text())
    assert data["capture"] == {"max_files_per_handoff": 7} and data["llm"]["provider"] == "none"
    assert svc.ws.load_config().capture.max_files_per_handoff == 7


def test_cloud_provider_is_reported_not_overwritten(svc: ProjectService) -> None:
    svc.ws.config_path.write_text(json.dumps({"llm": {"provider": "groq"}}))
    assert svc.load_ai_settings().mode == "cloud"


def test_template_mode_never_calls_an_llm(svc: ProjectService) -> None:
    svc.save_ai_settings("off")
    prepared = svc.prepare_handoff("claude", "generic")
    assert prepared.handoff.method == "deterministic" and prepared.handoff.attempts == []


def test_check_ollama_when_not_running() -> None:
    result = check_ollama("http://127.0.0.1:9", "qwen2.5-coder:7b")
    assert not result.ok and "isn't running" in result.message


def test_check_ollama_model_missing_and_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    import ollama

    def listing(*names: str) -> object:
        models = [types.SimpleNamespace(model=n) for n in names]
        return types.SimpleNamespace(models=models)

    class Fake:
        names: tuple[str, ...] = ("other:1b",)

        def __init__(self, **_kw: object) -> None:
            pass

        def list(self) -> object:
            return listing(*Fake.names)

    monkeypatch.setattr(ollama, "Client", Fake)
    missing = check_ollama("http://h", "qwen2.5-coder:7b")
    assert not missing.ok and "ollama pull qwen2.5-coder:7b" in missing.message
    Fake.names = ("qwen2.5-coder:7b",)
    assert check_ollama("http://h", "qwen2.5-coder:7b").ok
    Fake.names = ("llama3:latest",)
    assert check_ollama("http://h", "llama3").ok


def test_git_is_installed_here() -> None:
    assert git_is_installed()  # the test suite itself needs git


def test_git_missing_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def boom(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert not git_is_installed()
