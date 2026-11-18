"""Exercise the engine without loading desktop or CLI frameworks."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from contextbridge.errors import UserError
from contextbridge.main import app
from contextbridge.service import ChatService, ProjectService


def test_engine_works_when_frontend_imports_are_blocked(tmp_path: Path) -> None:
    script = """
import importlib.abc
import sys
from pathlib import Path

class BlockFrontend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        frontend = fullname.split('.')[0] in {'PySide6', 'typer', 'rich'}
        if frontend or fullname.startswith(('contextbridge.gui', 'contextbridge.cli')):
            raise ImportError('Frontend import blocked: ' + fullname)

sys.meta_path.insert(0, BlockFrontend())
from contextbridge.service import ChatService, ProjectService
service = ProjectService(Path(sys.argv[1]))
service.init(install_hook=False)
service.save_ai_settings('off')
service.add_note('Use SQLite', 'architecture')
assert service.search('SQLite')
snapshot = service.snapshot()
assert 'Use SQLite' in service.prepare_handoff('claude', 'generic').handoff.payload
assert service.guide_content(snapshot).progress
assert service.project_map() is not None
assert 'hello' in ChatService.prepare('hello', 'continue', '').text
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_document_reads_are_bounded_sanitized_and_confined(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    service = ProjectService(root)
    document = root / "README.md"
    document.write_text("AKIAIOSFODNN7EXAMPLE\n" + "x" * 128001)
    text = service.read_document(document)
    assert "AKIAIOSFODNN7EXAMPLE" not in text
    assert text.endswith("Document excerpt limited to 128,000 characters.")
    outside = tmp_path / "outside.md"
    outside.write_text("private")
    with pytest.raises(UserError):
        service.read_document(outside)
    (root / "linked.md").symlink_to(outside)
    with pytest.raises(UserError):
        service.read_document(root / "linked.md")


def test_cli_notes_status_and_search_use_service(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ProjectService(git_repo)
    service.init(install_hook=False)
    calls: list[str] = []
    for name in ("add_note", "snapshot", "search"):
        original = getattr(ProjectService, name)

        def record(self, *args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(ProjectService, name, record)
    runner = CliRunner()
    for args in (["log", "SQLite decision"], ["status"], ["search", "SQLite"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    assert calls == ["add_note", "snapshot", "search"]


def test_chat_service_import_and_prepare_need_no_project(tmp_path: Path) -> None:
    path = tmp_path / "chat.md"
    path.write_text("user: Keep SQLite\nassistant: Agreed")
    imported = ChatService.import_file(path)
    packet = ChatService.prepare(imported.text, "Build next feature", "Keep SQLite")
    assert "Build next feature" in packet.text
    assert "Keep SQLite" in packet.text
    assert list(tmp_path.iterdir()) == [path]


def test_commit_capture_is_available_through_engine(git_repo: Path) -> None:
    from tests.helpers import commit_all, write

    service = ProjectService(git_repo)
    service.init(install_hook=False)
    write(git_repo, "feature.py", "def feature():\n    return 42\n")
    commit_all(git_repo, "add feature")
    assert service.ingest_head() > 0
    assert service.ingest_head() == 0
    snapshot = service.snapshot(include_worktree=False)
    assert any(item.path == "feature.py" for item in snapshot.context.files)
