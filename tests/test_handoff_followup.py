"""Handoff ranking, provider attribution, and clipboard presentation."""

from pathlib import Path

from typer.testing import CliRunner

from contextbridge import clipboard
from contextbridge.service import ProjectService
from contextbridge.synthesis.compressor import project_sections
from contextbridge.synthesis.facts import project_facts


def setup(root: Path) -> ProjectService:
    (root / "pyproject.toml").write_text(
        '[project]\nname="example"\nversion="0.1"\ndescription="Study planner"\n'
        '[project.scripts]\ncb="custom.bootstrap:run"\n'
        '[project.optional-dependencies]\ngui=["PySide6-Essentials>=6.12", "pypdf>=6"]\n'
    )
    service = ProjectService(root)
    service.init(install_hook=False)
    service.save_ai_settings("off")
    return service


def test_importance_ranking_limit_and_purpose_skip_trivial_changes(tmp_path):
    service = setup(tmp_path)
    with service.ws.open_db() as db:
        session, _ = service.ws.active_session(db)
        for path, diff in [
            ("custom/bootstrap.py", "+5 / -3 lines"),
            ("core/engine.py", "+30 / -10 lines"),
            ("tiny.py", "+1 / -1 lines"),
            ("huge.py", "+200 / -50 lines"),
        ]:
            db.add_file_change(session.id, path, "modified", diff_snippet=diff)
        for i in range(20):
            db.add_file_change(
                session.id, f"module_{i}.py", "modified", diff_snippet="+3 / -2 lines"
            )
    prepared = service.prepare_handoff(to_agent="codex", use_llm=False)
    files = project_sections(prepared.context)["Files to read first"]
    assert len(files) == 8 and len(prepared.active_files) == 8
    assert files[0].startswith("custom/bootstrap.py")
    assert files[1].startswith("huge.py")  # substantial changes outrank a small core edit
    assert files[2].startswith("core/engine.py")
    assert all("tiny.py" not in item for item in files)
    assert "module" in files[0] or "entry point" in files[0]
    assert "1 / -1" not in "\n".join(files)


def test_optional_gui_dependencies_are_in_stack(tmp_path):
    setup(tmp_path)
    _, stack = project_facts(tmp_path)
    assert "PySide6-Essentials" in stack and "pypdf" in stack and "optional gui" in stack


def test_unknown_source_never_guessed_from_previous_destination(tmp_path, monkeypatch):
    service = setup(tmp_path)
    for name in ("CB_AGENT", "CODEX_THREAD_ID", "CLAUDECODE"):
        monkeypatch.delenv(name, raising=False)
    state = service.ws.load_state()
    state.last_handoff = {"to": "claude"}
    service.ws.save_state(state)
    assert service.prepare_handoff(to_agent="codex", use_llm=False).from_label == "unknown"
    messages = []
    service.prepare_handoff("codex", "codex", use_llm=False, notify=messages.append)
    assert any("same" in message for message in messages)


def test_done_and_copy_have_no_old_prefix_or_dry_run_label(tmp_path, monkeypatch):
    from contextbridge.main import app

    service = setup(tmp_path)
    service.add_note("Done: Implemented chat imports.")
    text = service.prepare_handoff(use_llm=False).handoff.payload
    assert "Implemented chat imports." in text
    assert "Logged activity" not in text and "Done:" not in text
    assert "Change log records" not in text
    copied = []
    monkeypatch.setattr(clipboard, "copy_text", copied.append)
    result = CliRunner().invoke(app, ["--repo", str(tmp_path), "handoff", "--copy", "--no-llm"])
    assert result.exit_code == 0, result.output
    assert "dry run" not in result.stdout.lower() and copied


def test_large_live_change_ranks_using_full_diff_counts(tmp_path):
    import subprocess

    service = setup(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.test"], check=True
    )
    large = tmp_path / "large_feature.py"
    large.write_text("value = 0\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "baseline"], check=True)
    # Long lines ensure the display snippet holds far fewer changes than the real patch.
    large.write_text("".join(f'value_{number} = "' + "x" * 300 + '"\n' for number in range(100)))
    prepared = service.prepare_handoff(use_llm=False)
    item = next(file for file in prepared.context.files if file.path == "large_feature.py")
    assert item.line_delta == (100, 1)
    assert len(item.snippets[0]) < 10000
    files = project_sections(prepared.context)["Files to read first"]
    assert files[0].startswith("large_feature.py")
    assert "+100 / -1 lines" in files[0]
