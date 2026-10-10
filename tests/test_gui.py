"""The desktop app, driven the way a person would: click, type, read what appears."""

from __future__ import annotations

import json
import shlex
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QSettings, Qt, QTimer, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPlainTextEdit

from contextbridge.errors import ContextBridgeError
from contextbridge.gui.app import configure_app
from contextbridge.gui.mainwindow import NAV, MainWindow
from tests.gui_helpers import buttons, find_button, has_button, pump, settle
from tests.helpers import commit_all, write

PY = shlex.quote(sys.executable)
CART = "class Cart:\n    def total(self):\n        return sum(self.items)\n"
TESTS = "def test_ok():\n    assert 1\n\ndef test_bad():\n    assert 0, 'boom'\n"


@pytest.fixture(scope="session")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication(["pytest"])
    assert isinstance(app, QApplication)
    configure_app(app)
    yield app


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Use the offline token estimate, without leaking that choice into other tests.

    The tokenizer choice is cached on first use, so the cache is cleared on both sides.
    """
    from contextbridge.synthesis import token_counter

    monkeypatch.setenv("CB_TOKENIZER", "estimate")
    token_counter._encoding.cache_clear()
    yield
    monkeypatch.delenv("CB_TOKENIZER")
    token_counter._encoding.cache_clear()


class Shell:
    """A window plus the dialogs it tried to show."""

    def __init__(self, app: QApplication, window: MainWindow, settings_path: Path) -> None:
        self.app, self.window, self.settings_path = app, window, settings_path
        self.dialogs: list[tuple[str, str]] = []
        window.show_message = lambda title, text: self.dialogs.append((title, text))  # type: ignore[method-assign]

    def settle(self) -> None:
        settle(self.app, self.window)

    def open(self, path: Path) -> None:
        self.window.open_project(str(path))
        self.settle()

    def page(self, key: str) -> Any:
        self.window.go(key)
        pump(self.app)
        return self.window.page_map[key]

    def toasts(self) -> str:
        return self.window.toast_widget.text()


@pytest.fixture
def shell(qapp: QApplication, tmp_path: Path) -> Iterator[Shell]:
    settings_path = tmp_path / "settings.ini"
    window = MainWindow(
        QSettings(str(settings_path), QSettings.Format.IniFormat), restore_last=False
    )
    window.resize(1040, 720)
    window.show()
    sh = Shell(qapp, window, settings_path)
    sh.settle()
    yield sh
    window.runner.wait(5000)
    window.close()
    pump(qapp, 0.02)


@pytest.fixture
def project(git_repo: Path) -> Path:
    write(git_repo, "cart.py", CART)
    commit_all(git_repo, "add cart")
    return git_repo


@pytest.fixture
def ready(shell: Shell, project: Path) -> Shell:
    """A project that has been set up (through the real Set up button)."""
    shell.open(project)
    home = shell.page("home")
    find_button(home, "Set up this project").click()
    shell.settle()
    commit_all(project, "ignore .context/")
    shell.window.refresh()
    shell.settle()
    return shell


def db(project: Path, sql: str) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(project / ".context" / "state.db")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ----- starting up ------------------------------------------------------------------


def test_starts_on_the_welcome_screen(shell: Shell) -> None:
    assert shell.window.root.currentIndex() == 0
    assert has_button(shell.window.welcome, "Choose a project folder…")
    assert shell.window.windowTitle() == "ContextBridge"
    assert not shell.window.windowIcon().isNull()


def test_no_stylesheet_or_qt_warnings(qapp: QApplication, tmp_path: Path) -> None:
    messages: list[str] = []
    previous = qInstallMessageHandler(lambda _t, _c, msg: messages.append(msg))
    try:
        window = MainWindow(
            QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat), restore_last=False
        )
        window.show()
        for key in ("home", "notes", "changes", "handoff", "settings"):
            window.go(key)
            pump(qapp, 0.03)
        window.close()
    finally:
        qInstallMessageHandler(previous)
    bad = [m for m in messages if "stylesheet" in m.lower() or "parse" in m.lower()]
    assert not bad, bad


def test_git_missing_shows_a_friendly_notice(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("contextbridge.gui.mainwindow.git_is_installed", lambda: False)
    window = MainWindow(
        QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat), restore_last=False
    )
    window.show()
    settle(qapp, window)
    assert window.welcome.git_notice.isVisibleTo(window.welcome)
    window.close()


# ----- opening and setting up a project ---------------------------------------------


def test_opening_a_project_offers_one_click_setup(shell: Shell, project: Path) -> None:
    shell.open(project)
    assert shell.window.root.currentIndex() == 1
    assert shell.window.project_name.text() == project.name
    home = shell.page("home")
    assert has_button(home, "Set up this project")
    assert not (project / ".context").exists()  # nothing happens until you click


def test_setup_creates_workspace_and_installs_the_hook(shell: Shell, project: Path) -> None:
    shell.open(project)
    find_button(shell.page("home"), "Set up this project").click()
    shell.settle()
    assert (project / ".context" / "state.db").is_file()
    assert "contextbridge:start" not in (project / ".gitignore").read_text()
    assert ".context/" in (project / ".gitignore").read_text()
    assert "# >>> contextbridge >>>" in (project / ".git" / "hooks" / "post-commit").read_text()
    home = shell.page("home")
    assert has_button(home, "Hand off to another assistant")
    assert not has_button(home, "Set up this project")


def test_setup_can_skip_the_hook(shell: Shell, project: Path) -> None:
    shell.open(project)
    home = shell.page("home")
    assert home.hook_checkbox is not None and home.hook_checkbox.isChecked()
    home.hook_checkbox.setChecked(False)
    find_button(home, "Set up this project").click()
    shell.settle()
    assert (project / ".context").is_dir()
    assert not (project / ".git" / "hooks" / "post-commit").exists()


def test_setup_in_a_folder_without_git(shell: Shell, tmp_path: Path) -> None:
    folder = tmp_path / "just-notes"
    folder.mkdir()
    shell.open(folder)
    home = shell.page("home")
    assert home.hook_checkbox is not None and not home.hook_checkbox.isEnabled()
    find_button(home, "Set up this project").click()
    shell.settle()
    assert (folder / ".context" / "state.db").is_file()
    assert not shell.dialogs  # no error for a folder that isn't a git repo


def test_screens_wait_for_setup(shell: Shell, project: Path) -> None:
    shell.open(project)
    for key in ("notes", "changes", "handoff", "settings"):
        page = shell.page(key)
        assert has_button(page, "Go to Home to set up"), key
    notes = shell.page("notes")
    assert not notes.editor_card.isVisibleTo(notes)
    find_button(notes, "Go to Home to set up").click()
    assert shell.window.pages.currentWidget() is shell.window.page_map["home"]


# ----- notes ------------------------------------------------------------------------


def test_adding_a_note(ready: Shell, project: Path) -> None:
    notes = ready.page("notes")
    notes.editor.setPlainText("Prices are stored in cents")
    notes.chips.set_value("architecture")
    find_button(notes, "Save note").click()
    ready.settle()
    assert db(project, "SELECT content, tag FROM decision_logs") == [
        ("Prices are stored in cents", "architecture")
    ]
    assert notes.editor.toPlainText() == ""
    assert "Saved" in ready.toasts()
    texts = [lab.text() for lab in notes.findChildren(QLabel)]
    assert "Prices are stored in cents" in texts and "Decision" in texts


def test_every_note_kind_maps_to_its_tag(ready: Shell, project: Path) -> None:
    notes = ready.page("notes")
    for key in ("note", "architecture", "blocker", "todo", "bug"):
        notes.editor.setPlainText(f"a {key}")
        notes.chips.set_value(key)
        find_button(notes, "Save note").click()
        ready.settle()
    assert {t for (t,) in db(project, "SELECT tag FROM decision_logs")} == {
        "note", "architecture", "blocker", "todo", "bug",
    }  # fmt: skip


def test_empty_note_is_not_saved(ready: Shell, project: Path) -> None:
    notes = ready.page("notes")
    notes.editor.setPlainText("   ")
    find_button(notes, "Save note").click()
    ready.settle()
    assert db(project, "SELECT * FROM decision_logs") == []
    assert "Write something first" in ready.toasts()


def test_note_with_a_secret_is_hidden_and_explained(ready: Shell, project: Path) -> None:
    notes = ready.page("notes")
    notes.editor.setPlainText("The staging key is AKIAIOSFODNN7EXAMPLE")
    find_button(notes, "Save note").click()
    ready.settle()
    [(content,)] = db(project, "SELECT content FROM decision_logs")
    assert "AKIAIOSFODNN7EXAMPLE" not in content
    assert notes.redaction_notice.isVisibleTo(notes)
    assert "hid 1 value" in notes.redaction_notice._text.text()


def test_searching_notes(ready: Shell) -> None:
    svc = ready.window.service
    assert svc is not None
    svc.add_note("alpha decision", "architecture")
    svc.add_note("beta todo", "todo")
    ready.window.refresh()
    ready.settle()
    notes = ready.page("notes")

    def shown() -> list[str]:
        return [
            lab.text()
            for lab in notes.findChildren(QLabel)
            if lab.text() in ("alpha decision", "beta todo")
        ]

    assert sorted(shown()) == ["alpha decision", "beta todo"]
    notes.search.setText("beta")
    pump(ready.app)
    assert shown() == ["beta todo"]
    notes.search.setText("nothing like this")
    pump(ready.app)
    assert shown() == []
    assert any("No notes match" in lab.text() for lab in notes.findChildren(QLabel))


def test_empty_notes_screen_is_friendly(ready: Shell) -> None:
    notes = ready.page("notes")
    assert any("No notes yet" in lab.text() for lab in notes.findChildren(QLabel))


# ----- changes and tests ------------------------------------------------------------


def test_changes_lists_files_and_functions(ready: Shell, project: Path) -> None:
    write(project, "cart.py", CART.replace("sum(self.items)", "sum(i.cents for i in self.items)"))
    write(project, "discount.py", "def apply(code):\n    return code\n")
    ready.window.refresh()
    ready.settle()
    changes = ready.page("changes")
    texts = " | ".join(lab.text() for lab in changes.findChildren(QLabel))
    assert "cart.py" in texts and "discount.py" in texts
    assert "Functions: Cart.total" in texts and "apply (added)" in texts
    assert "Not saved to Git yet" in texts and "New" in texts and "Changed" in texts


def test_changes_hints_when_commits_are_not_remembered(ready: Shell, project: Path) -> None:
    changes = ready.page("changes")
    assert not changes.hook_hint.isVisibleTo(changes)  # setup installed the hook
    svc = ready.window.service
    assert svc is not None
    svc.uninstall_hook()
    ready.window.refresh()
    ready.settle()
    assert changes.hook_hint.isVisibleTo(changes)
    assert "aren't being remembered" in changes.hook_hint._text.text()


def test_test_command_is_prefilled_from_the_project(ready: Shell, project: Path) -> None:
    write(project, "go.mod", "module x\n")
    ready.window.page_map["changes"]._command_filled_for = None
    changes = ready.page("changes")
    ready.window.refresh()
    ready.settle()
    assert changes.command.text() == "go test ./..."


def test_running_tests_shows_output_and_result(ready: Shell, project: Path) -> None:
    write(project, "suite/test_x.py", TESTS)
    changes = ready.page("changes")
    changes.command.setText(f"{PY} -m pytest -q -p no:cacheprovider suite")
    find_button(changes, "Run my tests").click()
    assert not find_button(changes, "Running…").isEnabled()  # busy while it runs
    ready.settle()
    assert find_button(changes, "Run my tests").isEnabled()
    assert "boom" in changes.output.toPlainText()
    result = changes.result_notice._text.text()
    assert "1 test failing" in result and "suite/test_x.py::test_bad" in result
    [(passed, failed, command)] = db(project, "SELECT passed, failed, command FROM test_runs")
    assert (passed, failed) == (1, 1) and "pytest" in command
    home = ready.page("home")
    assert any("1 test failing" in lab.text() for lab in home.findChildren(QLabel))


def test_a_passing_run_says_so(ready: Shell, project: Path) -> None:
    write(project, "suite/test_x.py", "def test_ok():\n    assert True\n")
    changes = ready.page("changes")
    changes.command.setText(f"{PY} -m pytest -q -p no:cacheprovider suite")
    find_button(changes, "Run my tests").click()
    ready.settle()
    assert "All 1 test passing" in changes.result_notice._text.text()


def test_window_stays_responsive_while_tests_run(ready: Shell) -> None:
    """The slow command runs in the background: the interface keeps handling events."""
    changes = ready.page("changes")
    changes.command.setText(f"{PY} -c 'import time; time.sleep(1.2)'")
    ticks = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(time.time()))
    timer.start()
    start = time.time()
    find_button(changes, "Run my tests").click()
    assert time.time() - start < 0.5  # the click itself returned immediately
    assert ready.window.runner.busy
    ready.settle()
    timer.stop()
    assert len(ticks) >= 20, f"the interface only ticked {len(ticks)} times in ~1.2s"


def test_unknown_test_command_is_explained(ready: Shell) -> None:
    changes = ready.page("changes")
    changes.command.setText("definitely-not-a-command-xyz")
    find_button(changes, "Run my tests").click()
    ready.settle()
    assert "wasn't found" in changes.result_notice._text.text()


def test_blank_test_command_asks_for_one(ready: Shell, project: Path) -> None:
    changes = ready.page("changes")
    changes.command.setText("")
    find_button(changes, "Run my tests").click()
    ready.settle()
    assert "Type your test command" in ready.toasts()
    assert db(project, "SELECT * FROM test_runs") == []


# ----- hand off ---------------------------------------------------------------------


def _seed_notes(ready: Shell) -> None:
    svc = ready.window.service
    assert svc is not None
    svc.add_note("Prices are cents", "architecture")
    svc.add_note("Empty cart crashes checkout", "blocker")
    ready.window.refresh()
    ready.settle()


def test_preview_needs_a_choice(ready: Shell) -> None:
    handoff = ready.page("handoff")
    assert not find_button(handoff, "Preview the summary").isEnabled()
    handoff.choice_cards["cursor"].click()
    assert find_button(handoff, "Preview the summary").isEnabled()


@pytest.mark.parametrize(
    ("agent", "relative"),
    [("claude", "CLAUDE.md"), ("cursor", ".cursor/rules/handoff.mdc"), ("generic", "HANDOFF.md")],
)
def test_full_handoff_flow(ready: Shell, project: Path, agent: str, relative: str) -> None:
    _seed_notes(ready)
    handoff = ready.page("handoff")
    handoff.choice_cards[agent].click()
    find_button(handoff, "Preview the summary").click()
    ready.settle()
    assert handoff.preview_card.isVisibleTo(handoff)
    preview = handoff.preview_text.toPlainText()
    assert "Prices are cents" in preview and "Empty cart crashes checkout" in preview
    assert relative in handoff.preview_target.text()
    assert not (project / relative).exists()  # previewing writes nothing

    find_button(handoff, "Write the handoff").click()
    ready.settle()
    written = (project / relative).read_text()
    assert preview.strip() in written  # exactly what was previewed
    assert handoff.done_card.isVisibleTo(handoff)
    assert relative in handoff.done_text.text()
    rows = db(project, "SELECT to_agent, written_path FROM handoffs")
    assert rows == [(agent, relative)]
    state = json.loads((project / ".context" / "state.json").read_text())
    assert state["last_handoff"]["to"] == agent
    home = ready.page("home")
    assert any("Last handoff" in lab.text() for lab in home.findChildren(QLabel))


def test_handoff_twice_does_not_duplicate_and_keeps_user_content(
    ready: Shell, project: Path
) -> None:
    (project / "CLAUDE.md").write_text("# Team rules\n\n- be kind\n")
    _seed_notes(ready)
    for _ in range(2):
        handoff = ready.page("handoff")
        handoff._reset()
        ready.settle()
        handoff.choice_cards["claude"].click()
        find_button(handoff, "Preview the summary").click()
        ready.settle()
        find_button(handoff, "Write the handoff").click()
        ready.settle()
    text = (project / "CLAUDE.md").read_text()
    assert text.startswith("# Team rules\n\n- be kind\n")
    assert text.count("contextbridge:start") == 1


def test_handoff_summary_is_in_plain_words(ready: Shell) -> None:
    _seed_notes(ready)
    handoff = ready.page("handoff")
    handoff.choice_cards["claude"].click()
    find_button(handoff, "Preview the summary").click()
    ready.settle()
    shown = " ".join(
        [handoff.preview_size.text(), handoff.preview_how.text(), handoff.preview_target.text()]
    )
    assert "words" in shown and "token" not in shown.lower()
    assert "template" in handoff.preview_how.text()


def test_change_something_goes_back(ready: Shell, project: Path) -> None:
    handoff = ready.page("handoff")
    handoff.choice_cards["cursor"].click()
    find_button(handoff, "Preview the summary").click()
    ready.settle()
    find_button(handoff, "Change something").click()
    ready.settle()
    assert handoff.step1.isVisibleTo(handoff) and not handoff.preview_card.isVisibleTo(handoff)
    assert not (project / ".cursor").exists()


def test_existing_file_is_protected_until_you_agree(
    ready: Shell, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "HANDOFF.md").write_text("my own notes\n")
    handoff = ready.page("handoff")
    handoff.choice_cards["generic"].click()
    find_button(handoff, "Preview the summary").click()
    ready.settle()
    find_button(handoff, "Write the handoff").click()
    ready.settle()
    assert handoff.conflict_notice.isVisibleTo(handoff)
    assert "didn't create it" in handoff.conflict_notice._text.text()
    assert (project / "HANDOFF.md").read_text() == "my own notes\n"
    assert not ready.dialogs  # a calm inline message, not an error dialog

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel)
    )
    find_button(handoff, "Replace it").click()
    ready.settle()
    assert (project / "HANDOFF.md").read_text() == "my own notes\n"  # cancelled: untouched

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    find_button(handoff, "Replace it").click()
    ready.settle()
    assert "contextbridge:start" in (project / "HANDOFF.md").read_text()
    assert handoff.done_card.isVisibleTo(handoff)


def test_done_screen_actions(ready: Shell, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "contextbridge.gui.pages.handoff.QDesktopServices.openUrl",
        staticmethod(lambda url: opened.append(url.toLocalFile()) or True),
    )
    handoff = ready.page("handoff")
    handoff.choice_cards["generic"].click()
    find_button(handoff, "Preview the summary").click()
    ready.settle()
    find_button(handoff, "Write the handoff").click()
    ready.settle()
    find_button(handoff, "Copy the summary").click()
    assert "# ContextBridge handoff" in QGuiApplication.clipboard().text()
    find_button(handoff, "Show the file").click()
    assert opened == [str(project.resolve())]
    find_button(handoff, "Start another handoff").click()
    ready.settle()
    assert handoff.step1.isVisibleTo(handoff) and not handoff.done_card.isVisibleTo(handoff)


def test_last_handoff_target_is_preselected(ready: Shell) -> None:
    handoff = ready.page("handoff")
    handoff.choice_cards["cursor"].click()
    find_button(handoff, "Preview the summary").click()
    ready.settle()
    find_button(handoff, "Write the handoff").click()
    ready.settle()
    find_button(handoff, "Start another handoff").click()
    ready.settle()
    assert handoff.selected_agent() == "cursor"


# ----- settings ---------------------------------------------------------------------


def test_ai_choice_is_saved_and_used(ready: Shell, project: Path) -> None:
    settings = ready.page("settings")
    assert settings.local_radio.isChecked() and settings.model_row.isVisibleTo(settings)
    settings.off_radio.setChecked(True)
    ready.settle()
    config = json.loads((project / ".context" / "config.json").read_text())
    assert config["llm"]["provider"] == "none"
    assert not settings.model_row.isVisibleTo(settings)
    handoff = ready.page("handoff")
    handoff.choice_cards["claude"].click()
    find_button(handoff, "Preview the summary").click()
    ready.settle()
    assert "No AI was needed" in handoff.preview_how.text()


def test_smart_mode_without_ollama_falls_back_quietly(ready: Shell) -> None:
    handoff = ready.page("handoff")
    handoff.choice_cards["claude"].click()
    find_button(handoff, "Preview the summary").click()
    ready.settle()
    assert "wasn't reachable" in handoff.preview_how.text()
    assert not ready.dialogs


def test_model_name_is_saved(ready: Shell, project: Path) -> None:
    settings = ready.page("settings")
    settings.model_edit.setText("llama3:8b")
    settings.model_edit.editingFinished.emit()
    ready.settle()
    assert (
        json.loads((project / ".context" / "config.json").read_text())["llm"]["model"]
        == "llama3:8b"
    )


def test_check_connection_reports_in_plain_words(ready: Shell) -> None:
    settings = ready.page("settings")
    find_button(settings, "Check connection").click()
    ready.settle()
    assert settings.ai_message.isVisibleTo(settings)
    assert "isn't running" in settings.ai_message._text.text()
    assert find_button(settings, "Check connection").isEnabled()


def test_cloud_setup_is_shown_read_only(ready: Shell, project: Path) -> None:
    (project / ".context" / "config.json").write_text(json.dumps({"llm": {"provider": "groq"}}))
    ready.window.refresh()
    ready.settle()
    settings = ready.page("settings")
    assert settings.cloud_notice.isVisibleTo(settings)
    assert not settings.local_radio.isVisibleTo(settings)


def test_commit_recording_toggle(ready: Shell, project: Path) -> None:
    settings = ready.page("settings")
    hook = project / ".git" / "hooks" / "post-commit"
    assert settings.hook_checkbox.isChecked() and hook.exists()
    settings.hook_checkbox.setChecked(False)
    ready.settle()
    assert not hook.exists()
    settings.hook_checkbox.setChecked(True)
    ready.settle()
    assert "contextbridge" in hook.read_text()


def test_existing_hook_that_cannot_be_extended_is_explained(ready: Shell, project: Path) -> None:
    svc = ready.window.service
    assert svc is not None
    svc.uninstall_hook()
    hook = project / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/usr/bin/env python3\nprint('mine')\n")
    hook.chmod(0o755)
    ready.window.refresh()
    ready.settle()
    settings = ready.page("settings")
    settings.hook_checkbox.setChecked(True)
    ready.settle()
    assert not settings.hook_checkbox.isChecked()
    assert "left alone" in settings.hook_note.text()
    assert hook.read_text() == "#!/usr/bin/env python3\nprint('mine')\n"


def test_start_a_new_work_session(ready: Shell, project: Path) -> None:
    before = json.loads((project / ".context" / "state.json").read_text())["active_session_id"]
    settings = ready.page("settings")
    find_button(settings, "Start a new work session").click()
    ready.settle()
    assert db(project, f"SELECT status FROM sessions WHERE id = '{before}'") == [("archived",)]
    assert "Fresh session started" in ready.toasts()


def test_remove_handoff_files(ready: Shell, project: Path) -> None:
    (project / "CLAUDE.md").write_text("# mine\n")
    handoff = ready.page("handoff")
    handoff.choice_cards["claude"].click()
    find_button(handoff, "Preview the summary").click()
    ready.settle()
    find_button(handoff, "Write the handoff").click()
    ready.settle()
    find_button(ready.page("settings"), "Remove the files I handed off").click()
    ready.settle()
    assert (project / "CLAUDE.md").read_text() == "# mine\n"
    assert "CLAUDE.md" in ready.toasts()


def _choose_in_delete_dialog(monkeypatch: pytest.MonkeyPatch, label: str) -> list[str]:
    seen: list[str] = []

    def exec_(self: QMessageBox) -> int:
        seen.append(self.informativeText())
        return 0

    def clicked(self: QMessageBox) -> Any:
        return next(b for b in self.buttons() if b.text() == label)

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    monkeypatch.setattr(QMessageBox, "clickedButton", clicked)
    return seen


def test_delete_everything_needs_confirmation(
    ready: Shell, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready.window.service.add_note("keep me")  # type: ignore[union-attr]
    seen = _choose_in_delete_dialog(monkeypatch, "Keep my data")
    find_button(ready.page("settings"), "Delete everything ContextBridge saved…").click()
    ready.settle()
    assert "1 note" in seen[0] and "can't be undone" in seen[0]
    assert db(project, "SELECT COUNT(*) FROM decision_logs") == [(1,)]

    _choose_in_delete_dialog(monkeypatch, "Delete everything")
    find_button(ready.page("settings"), "Delete everything ContextBridge saved…").click()
    ready.settle()
    assert db(project, "SELECT COUNT(*) FROM decision_logs") == [(0,)]
    assert "deleted" in ready.toasts()


# ----- navigation, memory, errors, polish -------------------------------------------


def test_keyboard_shortcuts_switch_screens(ready: Shell) -> None:
    ready.window.activateWindow()
    pump(ready.app)
    for key, expected in (
        (Qt.Key.Key_2, "notes"),
        (Qt.Key.Key_4, "handoff"),
        (Qt.Key.Key_1, "home"),
    ):
        QTest.keyClick(ready.window, key, Qt.KeyboardModifier.ControlModifier)
        pump(ready.app)
        assert ready.window.pages.currentWidget() is ready.window.page_map[expected], expected


def test_sidebar_selection_follows_navigation(ready: Shell) -> None:
    labels = dict(NAV)
    for key in ("notes", "changes", "handoff", "settings", "home"):
        find_button(ready.window.centralWidget(), labels[key]).click()
        assert ready.window.nav_buttons[key].isChecked()
        assert ready.window.pages.currentWidget() is ready.window.page_map[key]


def test_remembers_the_last_project(qapp: QApplication, shell: Shell, project: Path) -> None:
    shell.open(project)
    other = MainWindow(QSettings(str(shell.settings_path), QSettings.Format.IniFormat))
    other.show()
    settle(qapp, other)
    try:
        assert other.root.currentIndex() == 1 and other.service is not None
        assert other.service.root == project.resolve()
        assert other.recent_projects() == [str(project.resolve())]
    finally:
        other.close()


def test_recent_projects_are_listed_and_pruned(shell: Shell, project: Path, tmp_path: Path) -> None:
    second = tmp_path / "second"
    second.mkdir()
    shell.open(project)
    shell.open(second)
    recents = shell.window.recent_projects()
    assert recents[0] == str(second.resolve()) and str(project.resolve()) in recents
    assert has_button(shell.window.welcome, f"{second.name}   ·   {second.parent}")
    second.rmdir()
    assert str(second.resolve()) not in shell.window.recent_projects()


def test_opening_a_missing_folder_is_explained(shell: Shell, tmp_path: Path) -> None:
    shell.window.open_project(str(tmp_path / "gone"))
    assert shell.window.service is None and shell.window.root.currentIndex() == 0
    assert shell.dialogs and "isn't available" in shell.dialogs[0][1]


def test_errors_are_friendly(shell: Shell) -> None:
    shell.window.report_error(ContextBridgeError("Something clear.", hint="Try this."))
    title, text = shell.dialogs[-1]
    assert text == "Something clear.\n\nTry this." and "didn't work" in title
    shell.window.report_error(RuntimeError("secret internals /Users/x/token"))
    title, text = shell.dialogs[-1]
    assert "unexpected" in text and "RuntimeError" in text and "/Users/x" not in text


def test_refresh_ignores_stale_answers(ready: Shell) -> None:
    svc = ready.window.service
    assert svc is not None
    ready.window.refresh()
    first_ticket = ready.window._refresh_id
    ready.window.refresh()  # a newer request supersedes the first
    ready.settle()
    assert ready.window._refresh_id == first_ticket + 1 and ready.window.snapshot is not None


def test_every_screen_renders_at_the_minimum_size(ready: Shell, tmp_path: Path) -> None:
    ready.window.resize(880, 600)
    for key in ("home", "notes", "changes", "handoff", "settings"):
        page = ready.page(key)
        image = page.grab().toImage()
        assert image.width() > 300 and image.height() > 300, key


def test_controls_have_accessible_names(ready: Shell) -> None:
    for key in ("home", "notes", "changes", "handoff", "settings"):
        page = ready.page(key)
        for btn in buttons(page):
            assert btn.accessibleName() or btn.text().strip(), (key, btn.objectName())
    for btn in ready.window.nav_buttons.values():
        assert btn.accessibleName() and btn.toolTip()
    notes = ready.page("notes")
    assert notes.editor.accessibleName() == "Write a note"
    assert isinstance(notes.editor, QPlainTextEdit)


def test_no_focus_ring_is_left_on_a_button_after_clicking(ready: Shell) -> None:
    find_button(ready.window.centralWidget(), "Notes").click()
    assert ready.window.nav_buttons["notes"].focusPolicy() == Qt.FocusPolicy.TabFocus


def test_window_closes_cleanly_with_work_running(
    qapp: QApplication, shell: Shell, project: Path
) -> None:
    shell.open(project)
    find_button(shell.page("home"), "Set up this project").click()
    shell.window.close()  # closing while the setup task may still be running must not crash
    shell.window.runner.wait(5000)
    pump(qapp, 0.05)
