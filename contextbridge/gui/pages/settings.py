"""Settings: AI summaries, automatic commit notes, and starting fresh."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from contextbridge import __version__
from contextbridge.core.git_observer import HookAction
from contextbridge.gui import wording
from contextbridge.gui.pages.base import Host, ProjectPage
from contextbridge.gui.widgets import Card, Notice, button, label
from contextbridge.service import AISettings, OllamaCheck, Snapshot, check_ollama


class SettingsPage(ProjectPage):
    def __init__(self, host: Host, parent: QWidget | None = None) -> None:
        super().__init__(host, parent)
        self.heading("Settings", "Settings", "Everything here is optional. The defaults work well.")
        self._loading = False

        # ----- AI -------------------------------------------------------------------------------
        self.ai_card = Card()
        self.ai_card.body.addWidget(label("AI SUMMARIES", "Eyebrow"))
        self.ai_card.body.addWidget(label("How should your summary be written?", "H2"))
        self.local_radio = QRadioButton("Smart (recommended)")
        self.off_radio = QRadioButton("Simple template only")
        self.ai_group = QButtonGroup(self)
        self.ai_group.addButton(self.local_radio)
        self.ai_group.addButton(self.off_radio)
        self.local_radio.toggled.connect(self._ai_changed)
        self.ai_hint_smart = label(
            "Uses the free Ollama app on this computer when it's running, and a simple template when it isn't. "
            "Nothing ever leaves your computer.",
            "Small",
        )
        self.ai_hint_off = label("Always instant and private. Never uses an AI.", "Small")
        self.ai_card.body.addWidget(self.local_radio)
        self.ai_card.body.addWidget(self.ai_hint_smart)
        self.model_row = QWidget()
        mrow = QHBoxLayout(self.model_row)
        mrow.setContentsMargins(0, 4, 0, 0)
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("Model name, for example qwen2.5-coder:7b")
        self.model_edit.setAccessibleName("AI model name")
        self.model_edit.editingFinished.connect(self._ai_changed)
        self.check_button = button("Check connection")
        self.check_button.clicked.connect(self._check_ai)
        mrow.addWidget(self.model_edit, 1)
        mrow.addWidget(self.check_button)
        self.ai_card.body.addWidget(self.model_row)
        self.ai_message = Notice("")
        self.ai_message.hide()
        self.ai_card.body.addWidget(self.ai_message)
        self.ai_card.body.addSpacing(6)
        self.ai_card.body.addWidget(self.off_radio)
        self.ai_card.body.addWidget(self.ai_hint_off)
        self.cloud_notice = Notice(
            "A cloud AI is set up for this project through the command line. The app doesn't change it."
        )
        self.cloud_notice.hide()
        self.ai_card.body.addWidget(self.cloud_notice)
        self.body.addWidget(self.ai_card)

        # ----- commits -----------------------------------------------------------------------------
        self.hook_card = Card()
        self.hook_card.body.addWidget(label("COMMITS", "Eyebrow"))
        self.hook_checkbox = QCheckBox("Remember my commits automatically")
        self.hook_checkbox.toggled.connect(self._hook_toggled)
        self.hook_card.body.addWidget(self.hook_checkbox)
        self.hook_note = label("", "Small")
        self.hook_card.body.addWidget(self.hook_note)
        self.body.addWidget(self.hook_card)

        # ----- start fresh -----------------------------------------------------------------------
        self.fresh_card = Card()
        self.fresh_card.body.addWidget(label("START FRESH", "Eyebrow"))
        self.fresh_card.body.addWidget(label("Tidy up", "H2"))
        for text, hint, handler, kind in (
            (
                "Start a new work session",
                "Keeps everything, but begins a clean page for new work.",
                self._archive,
                "",
            ),
            (
                "Remove the files I handed off",
                "Takes ContextBridge's summary out of CLAUDE.md, Cursor and HANDOFF.md. Your own writing stays.",
                self._cleanup,
                "",
            ),
            (
                "Delete everything ContextBridge saved…",
                "Removes all notes, history and handoffs for this project. This can't be undone.",
                self._delete_all,
                "Danger",
            ),
        ):
            row = QVBoxLayout()
            row.setSpacing(2)
            btn = button(text, kind)
            btn.clicked.connect(handler)
            hint_label = label(hint, "Small")
            row.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
            row.addWidget(hint_label)
            self.fresh_card.body.addSpacing(6)
            self.fresh_card.body.addLayout(row)
        self.body.addWidget(self.fresh_card)

        # ----- about --------------------------------------------------------------------------------
        self.about_card = Card()
        self.about_card.body.addWidget(label("ABOUT", "Eyebrow"))
        self.about_label = label(f"ContextBridge {__version__}", "Muted")
        self.about_card.body.addWidget(self.about_label)
        self.folder_label = label("", "Small")
        self.about_card.body.addWidget(self.folder_label)
        open_folder = button("Show ContextBridge's data folder", "Link")
        open_folder.clicked.connect(self._open_data_folder)
        self.about_card.body.addWidget(open_folder)
        self.body.addWidget(self.about_card)
        self.body.addStretch(1)

        self.setup_holder = QWidget()
        self.setup_holder.setObjectName("Page")
        lay = QVBoxLayout(self.setup_holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.setup_prompt())
        self.body.insertWidget(3, self.setup_holder)

    # ----- loading ------------------------------------------------------------------------------------

    def refresh(self, snap: Snapshot | None) -> None:
        svc = self.service
        setup = self.needs_setup()
        self.setup_holder.setVisible(setup)
        for card in (self.ai_card, self.hook_card, self.fresh_card):
            card.setVisible(not setup)
        self.about_card.setVisible(svc is not None)
        if svc is None:
            return
        self.folder_label.setText(f"Project: {svc.root}")
        if setup:
            return
        self._loading = True
        try:
            ai: AISettings = svc.load_ai_settings()
            cloud = ai.mode == "cloud"
            for widget in (self.local_radio, self.off_radio, self.ai_hint_smart, self.ai_hint_off):
                widget.setVisible(not cloud)
            self.off_radio.setChecked(ai.mode == "off")
            self.local_radio.setChecked(ai.mode == "local")
            self.model_edit.setText(ai.model)
            self.model_row.setVisible(ai.mode == "local")
            self.cloud_notice.setVisible(cloud)
            is_repo = svc.is_git_repo()
            self.hook_checkbox.setEnabled(is_repo)
            self.hook_checkbox.setChecked(is_repo and svc.hook_installed())
            self.hook_note.setText(
                "Each commit is noted down in the background, so your summary stays current."
                if is_repo
                else "This folder isn't tracked by Git, so there are no commits to remember."
            )
        finally:
            self._loading = False

    # ----- AI ------------------------------------------------------------------------------------------

    def _ai_changed(self, *_args: object) -> None:
        svc = self.service
        if self._loading or svc is None:
            return
        local = self.local_radio.isChecked()
        self.model_row.setVisible(local)
        model = self.model_edit.text().strip()

        def work(_emit: object) -> None:
            svc.save_ai_settings("local" if local else "off", model or None)

        def done(_result: object) -> None:
            self.host.toast("Saved.")

        self.host.runner.run(work, on_done=done, on_error=self.host.report_error)

    def _check_ai(self) -> None:
        svc = self.service
        if svc is None:
            return
        model = self.model_edit.text().strip() or svc.load_ai_settings().model
        host_url = svc.load_ai_settings().host
        self.check_button.setEnabled(False)
        self.check_button.setText("Checking…")

        def work(_emit: object) -> OllamaCheck:
            return check_ollama(host_url, model)

        def done(result: OllamaCheck) -> None:
            self.check_button.setEnabled(True)
            self.check_button.setText("Check connection")
            self.ai_message.set_text(("✓  " if result.ok else "") + result.message)
            self.ai_message.show()

        self.host.runner.run(work, on_done=done, on_error=self.host.report_error)

    # ----- commits -----------------------------------------------------------------------------------------

    def _hook_toggled(self, checked: bool) -> None:
        svc = self.service
        if self._loading or svc is None:
            return

        def work(_emit: object) -> object:
            return svc.install_hook() if checked else svc.uninstall_hook()

        def done(result: object) -> None:
            action = getattr(result, "action", None)
            if checked and action in (HookAction.disabled_hook, HookAction.unsupported_shell):
                self._loading = True
                self.hook_checkbox.setChecked(False)
                self._loading = False
                self.hook_note.setText(
                    "You already have a Git helper here that ContextBridge can't safely add to, so it was left alone."
                )
                return
            self.host.toast(
                "Commits will be remembered." if checked else "Automatic commit notes are off."
            )

        self.host.runner.run(work, on_done=done, on_error=self.host.report_error)

    # ----- start fresh ---------------------------------------------------------------------------------------

    def _archive(self) -> None:
        svc = self.service
        if svc is None:
            return

        def work(_emit: object) -> object:
            return svc.archive_session()

        def done(session: object) -> None:
            self.host.toast("Fresh session started." if session else "There was no active session.")
            self.host.refresh()

        self.host.runner.run(work, on_done=done, on_error=self.host.report_error)

    def _cleanup(self) -> None:
        svc = self.service
        if svc is None:
            return

        def work(_emit: object) -> list[str]:
            return svc.cleanup_adapters()

        def done(removed: list[str]) -> None:
            self.host.toast(
                ("Removed from " + ", ".join(removed) + ".") if removed else "Nothing to remove."
            )
            self.host.refresh()

        self.host.runner.run(work, on_done=done, on_error=self.host.report_error)

    def _delete_all(self) -> None:
        svc = self.service
        if svc is None:
            return
        try:
            counts = svc.count_all()
        except Exception as exc:
            self.host.report_error(exc)
            return
        parts = [
            wording.plural(counts.get("decision_logs", 0), "note"),
            wording.plural(counts.get("file_changes", 0), "recorded change"),
            wording.plural(counts.get("test_runs", 0), "test run"),
            wording.plural(counts.get("handoffs", 0), "handoff"),
        ]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Delete everything?")
        box.setText("Delete everything ContextBridge saved for this project?")
        box.setInformativeText(
            "This permanently removes " + ", ".join(parts) + ".\n\nYour code, your files and "
            "any handoff files already written are not touched. This can't be undone."
        )
        cancel = box.addButton("Keep my data", QMessageBox.ButtonRole.RejectRole)
        delete = box.addButton("Delete everything", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is not delete:
            return

        def work(_emit: object) -> object:
            return svc.delete_all()

        def done(_result: object) -> None:
            self.host.toast("Everything was deleted.")
            self.host.refresh()

        self.host.runner.run(work, on_done=done, on_error=self.host.report_error)

    def _open_data_folder(self) -> None:
        svc = self.service
        if svc is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(svc.ws.context_dir)))
