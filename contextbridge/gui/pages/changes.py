"""Changes: what changed in the project, and a one-click way to run its tests."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from contextbridge.gui import wording
from contextbridge.gui.pages.base import Host, ProjectPage
from contextbridge.gui.widgets import Card, EmptyState, Notice, Spinner, button, clear_layout, label
from contextbridge.service import Snapshot, TestOutcome, detect_test_command
from contextbridge.synthesis.context_builder import FileSummary, describe_test_run, format_symbols


class ChangesPage(ProjectPage):
    MAX_OUTPUT_LINES = 3000

    def __init__(self, host: Host, parent: QWidget | None = None) -> None:
        super().__init__(host, parent)
        self.heading("Changes", "What's changed", "The files and functions you've been working on.")
        self.hook_hint = Notice("")
        self.hook_hint.hide()
        self.body.addWidget(self.hook_hint)
        self.files_box = QVBoxLayout()
        self.files_box.setSpacing(10)
        self.body.addLayout(self.files_box)

        self.body.addSpacing(8)
        self.body.addWidget(label("TESTS", "Eyebrow"))
        self.tests_card = Card()
        self.tests_card.body.addWidget(label("Run your project's tests", "H2"))
        self.tests_card.body.addWidget(
            label(
                "ContextBridge runs it for you and remembers the result, so the next assistant knows what's passing.",
                "Muted",
            )
        )
        self.command = QLineEdit()
        self.command.setPlaceholderText("Test command, for example: pytest")
        self.command.setAccessibleName("Test command")
        self.command.returnPressed.connect(self._run)
        self.run_button = button("Run my tests", "Primary")
        self.run_button.clicked.connect(self._run)
        row = QHBoxLayout()
        row.addWidget(self.command, 1)
        row.addWidget(self.run_button)
        self.tests_card.body.addSpacing(4)
        self.tests_card.body.addLayout(row)
        self.spinner = Spinner()
        self.tests_card.body.addWidget(self.spinner)
        self.result_notice = Notice("")
        self.result_notice.hide()
        self.tests_card.body.addWidget(self.result_notice)
        self.output = QPlainTextEdit()
        self.output.setObjectName("Output")
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(self.MAX_OUTPUT_LINES)
        self.output.setMinimumHeight(220)
        self.output.setAccessibleName("Test output")
        self.output.hide()
        self.tests_card.body.addWidget(self.output)
        self.body.addWidget(self.tests_card)
        self.body.addStretch(1)
        self._command_filled_for: str | None = None

    # ----- files ------------------------------------------------------------------------------

    def refresh(self, snap: Snapshot | None) -> None:
        clear_layout(self.files_box)
        setup = self.needs_setup()
        self.tests_card.setVisible(not setup)
        if setup:
            self.files_box.addWidget(self.setup_prompt())
            return
        svc = self.service
        assert svc is not None
        if self._command_filled_for != str(svc.root):
            self._command_filled_for = str(svc.root)
            self.command.setText(detect_test_command(svc.root) or "")
        remembers = not svc.is_git_repo() or svc.hook_installed()
        self.hook_hint.setVisible(not remembers)
        if not remembers:
            self.hook_hint.set_text(
                "Your commits aren't being remembered yet, so only changes you haven't committed show up here. "
                'Turn on "Remember my commits automatically" in Settings.'
            )
        if snap is None:
            self.files_box.addWidget(label("Looking at your project…", "Muted"))
            return
        ctx = snap.context
        if not ctx.files:
            self.files_box.addWidget(
                EmptyState(
                    "changes",
                    "No changes yet",
                    "Edit some files or make a commit, and they'll show up here.",
                )
            )
        for f in ctx.files:
            self.files_box.addWidget(self._file_card(f))
        if ctx.omitted_files:
            self.files_box.addWidget(
                label(f"…and {wording.plural(ctx.omitted_files, 'more file')}.", "Muted")
            )
        if ctx.test_run is not None and not self.output.isVisible():
            self.result_notice.set_text(
                f"Last run: {describe_test_run(ctx.test_run)} · {wording.when(ctx.test_run.timestamp)}"
            )
            self.result_notice.show()

    def _file_card(self, f: FileSummary) -> Card:
        card = Card()
        top = QHBoxLayout()
        top.addWidget(label(f.path, "H2", wrap=False))
        top.addStretch(1)
        tag = label(
            wording.CHANGE_LABELS.get(f.change_type, f.change_type.title()), "Tag", wrap=False
        )
        top.addWidget(tag)
        card.body.addLayout(top)
        if f.uncommitted:
            card.body.addWidget(label("Not saved to Git yet", "Small"))
        if f.symbols:
            card.body.addWidget(label("Functions: " + format_symbols(f.symbols, limit=8), "Muted"))
        return card

    # ----- running tests --------------------------------------------------------------------

    def _run(self) -> None:
        svc = self.service
        command = self.command.text().strip()
        if svc is None or self.needs_setup():
            return
        if not command:
            self.host.toast("Type your test command first, for example: pytest")
            return
        self.run_button.setEnabled(False)
        self.run_button.setText("Running…")
        self.spinner.show()
        self.output.clear()
        self.output.show()
        self.result_notice.hide()

        def work(emit: object) -> TestOutcome:
            return svc.run_tests(command, on_output=emit)  # type: ignore[arg-type]

        def progress(text: str) -> None:
            self.output.moveCursor(self.output.textCursor().MoveOperation.End)
            self.output.insertPlainText(text)
            self.output.ensureCursorVisible()

        def finish() -> None:
            self.spinner.hide()
            self.run_button.setEnabled(True)
            self.run_button.setText("Run my tests")

        def done(outcome: TestOutcome) -> None:
            finish()
            run = outcome.run
            if outcome.result.exit_code == 127:
                text = (
                    "That command wasn't found. Check the spelling, or that the tool is installed."
                )
            else:
                sentence, ok = wording.test_summary(run)
                text = ("✓  " if ok else "") + sentence
                if ok is False and run.failures:
                    names = [
                        str(f.get("test", ""))
                        for f in run.failures[:5]
                        if f.get("test") and f.get("test") != "(output tail)"
                    ]
                    if names:
                        text += "\n" + "\n".join(f"•  {n}" for n in names)
            self.result_notice.set_text(text)
            self.result_notice.show()
            self.host.refresh()

        def failed(exc: Exception) -> None:
            finish()
            self.host.report_error(exc)

        self.host.runner.run(work, on_done=done, on_error=failed, on_progress=progress)
