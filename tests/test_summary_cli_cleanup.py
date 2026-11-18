"""Provider CLI diagnostics and non-destructive todo/session cleanup."""

from typer.testing import CliRunner

from contextbridge.config import save_config
from contextbridge.main import app
from contextbridge.providers import credentials
from contextbridge.providers.http import HTTPProvider
from contextbridge.service import ProjectService
from contextbridge.synthesis.compressor import project_sections

runner = CliRunner()


def test_provider_show_never_reads_or_prints_the_key(git_repo, monkeypatch):
    project = ProjectService(git_repo)
    project.init(install_hook=False)
    save_config(
        {
            "llm": {
                "provider": "openai-compatible",
                "model": "openai/gpt-oss-120b",
                "base_url": "https://api.groq.com/openai/v1",
            }
        },
        project.ws.context_dir / "config.json",
    )
    monkeypatch.setattr(
        credentials, "get_key", lambda *_: (_ for _ in ()).throw(AssertionError("key read"))
    )
    result = runner.invoke(app, ["provider", "show"])
    assert result.exit_code == 0, result.output
    assert "openai/gpt-oss-120b" in result.output and "api.groq.com" in result.output


def test_provider_test_generates_with_effective_model(git_repo, monkeypatch):
    project = ProjectService(git_repo)
    project.init(install_hook=False)
    save_config(
        {
            "llm": {
                "provider": "openai-compatible",
                "model": "openai/gpt-oss-120b",
                "base_url": "https://api.groq.com/openai/v1",
            }
        },
        project.ws.context_dir / "config.json",
    )
    monkeypatch.setattr(credentials, "get_key", lambda endpoint: "private-key")
    calls = []

    def request(self, path, body):
        calls.append((self.model, path, body))
        return {"choices": [{"message": {"content": "OK"}}]}

    monkeypatch.setattr(HTTPProvider, "request", request)
    result = runner.invoke(app, ["provider", "test"])
    assert result.exit_code == 0, result.output
    assert calls[0][0] == "openai/gpt-oss-120b" and calls[0][2]["max_completion_tokens"] == 1024
    assert "private-key" not in result.output


def test_provider_commands_work_without_project_initialization(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for args in (["provider", "show"], ["provider", "test"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    assert not (tmp_path / ".contextbridge").exists()


def test_todo_resolve_dismiss_supersede_preserve_history_and_remove_stale_items(git_repo):
    service = ProjectService(git_repo)
    service.init(install_hook=False)
    done = service.add_note("Implement provider setup", "todo").decision
    obsolete = service.add_note("Use the abandoned SDK", "todo").decision
    old = service.add_note("Use the old model", "todo").decision
    replacement = service.add_note("Use the selected model", "todo").decision
    for args in (
        ["todo", "resolve", done.id],
        ["todo", "dismiss", obsolete.id],
        ["todo", "supersede", old.id, replacement.id],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    current = runner.invoke(app, ["todo", "list"])
    assert "Use the selected model" in current.output and "Use the old model" not in current.output
    history = runner.invoke(app, ["todo", "list", "--all"])
    assert all(word in history.output for word in ("resolved", "dismissed", "superseded"))
    prepared = service.prepare_handoff(to_agent="codex", use_llm=False)
    sections = project_sections(prepared.context)
    assert sections["Next steps"] == ["Use the selected model"]
    assert "Use the old model" not in prepared.handoff.payload
    assert "abandoned SDK" not in prepared.handoff.payload
    assert not any("Resolved todo" in item for item in sections["Done"])
    with service.ws.open_db() as db:
        assert len(db.list_decisions(prepared.session_id)) == 4


def test_duplicate_work_is_removed_and_archived_sessions_only_get_one_line(git_repo):
    service = ProjectService(git_repo)
    service.init(install_hook=False)
    with service.ws.open_db() as db:
        old = db.create_session("old-feature")
        db.add_decision(old.id, "Added the old feature. " + "Detailed implementation notes. " * 30)
        db.set_session_status(old.id, "archived")
    service.add_note("Done: Added fresh functionality.")
    service.add_note("Done: Added fresh functionality.")
    prepared = service.prepare_handoff(to_agent="codex", use_llm=False)
    sections = project_sections(prepared.context)
    assert sections["Done"].count("Added fresh functionality.") == 1
    archived = [item for item in sections["Done"] if "Earlier session" in item]
    assert len(archived) == 1 and "old feature" in archived[0]
    assert "Detailed implementation" not in archived[0]


def test_superseding_a_note_removes_its_old_claim_from_handoff(git_repo):
    service = ProjectService(git_repo)
    service.init(install_hook=False)
    old = service.add_note("Done: Used the old rendering path.").decision
    new = service.add_note("Done: Replaced the renderer with the current implementation.").decision
    result = runner.invoke(app, ["todo", "supersede", old.id, new.id])
    assert result.exit_code == 0
    payload = service.prepare_handoff(to_agent="codex", use_llm=False).handoff.payload
    assert "old rendering path" not in payload and "current implementation" in payload


def test_provider_show_rejects_a_url_containing_credentials(git_repo):
    project = ProjectService(git_repo)
    project.init(install_hook=False)
    save_config(
        {
            "llm": {
                "provider": "openai-compatible",
                "base_url": "https://user:do-not-print@example.test/v1",
            }
        },
        project.ws.context_dir / "config.json",
    )
    result = runner.invoke(app, ["provider", "show"])
    assert result.exit_code == 2 and "do-not-print" not in result.output
