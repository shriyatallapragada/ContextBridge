from pathlib import Path

from contextbridge.project_map import scan_project, visible_map


def test_static_imports_and_relative_imports_without_execution(tmp_path: Path) -> None:
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "ui.py").write_text('from . import service\nraise RuntimeError("never execute")')
    (package / "service.py").write_text("from app import storage")
    (package / "storage.py").write_text("")
    model = scan_project(tmp_path)
    assert ("app/ui.py", "app/service.py") in model.edges
    assert ("app/service.py", "app/storage.py") in model.edges
    assert model.scanned == 4
    assert len(visible_map(model, 2).nodes) == 2
    assert visible_map(model, 2).limited


def test_map_skips_external_symlinks_large_and_invalid_files(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def !!!")
    (tmp_path / "large.py").write_text("x" * 70000)
    (tmp_path / "external.py").symlink_to("/etc/passwd")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("")
    model = scan_project(tmp_path)
    assert model.scanned == 0 and model.skipped == 2
