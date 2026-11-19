"""Settings: AI summaries, automatic commit notes, and starting fresh."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from contextbridge import __version__
from contextbridge.core.git_observer import HookAction
from contextbridge.gui import wording
from contextbridge.gui.pages.base import Host, ProjectPage
from contextbridge.gui.widgets import Card, button, label
from contextbridge.service import Snapshot


class SettingsPage(ProjectPage):
    def __init__(self, host: Host, parent: QWidget | None = None) -> None:
        super().__init__(host, parent)
        self.heading("Settings", "Settings", "Everything here is optional. The defaults work well.")
        self._loading = False

        self.ai_card = Card()
        self.ai_card.body.addWidget(label("OPTIONAL MODEL", "Eyebrow"))
        from contextbridge.gui.provider_form import ProviderForm
        from contextbridge.providers.settings import ProviderSettingsService

        self.provider_form = ProviderForm(
            host.runner, ProviderSettingsService(), lambda: self.host.toast("Saved.")
        )
        self.ai_card.body.addWidget(self.provider_form)
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
            from contextbridge.providers.settings import ProviderSettingsService

            self.provider_form.service = ProviderSettingsService(svc.ws.context_dir)
            self.provider_form.reload()
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
