"""Hand off: choose who takes over, preview the summary, write it."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from contextbridge.errors import AdapterConflictError
from contextbridge.gui import assets, wording
from contextbridge.gui.pages.base import Host, ProjectPage
from contextbridge.gui.widgets import Card, ChoiceCard, Notice, Spinner, button, label, pixmap_label
from contextbridge.service import CommittedHandoff, PreparedHandoff, Snapshot


class HandoffPage(ProjectPage):
    def __init__(self, host: Host, parent: QWidget | None = None) -> None:
        super().__init__(host, parent)
        self._prepared: PreparedHandoff | None = None
        self._committed: CommittedHandoff | None = None
        self.heading(
            "Hand off",
            "Hand off to another assistant",
            "Pick who's taking over. We'll write them a short summary they can read.",
        )

        self.setup_holder = QWidget()
        self.setup_holder.setObjectName("Page")
        self.body.addWidget(self.setup_holder)

        # --- step 1
        self.step1 = QWidget()
        self.step1.setObjectName("Page")
        step1_layout = self._column(self.step1)
        step1_layout.addWidget(label("1  ·  WHO'S TAKING OVER?", "Eyebrow"))
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.choice_cards: dict[str, ChoiceCard] = {}
        for agent in wording.AGENTS:
            card = ChoiceCard(agent.label, agent.blurb, agent.file)
            card.clicked.connect(self._choice_changed)
            self.group.addButton(card)
            self.choice_cards[agent.key] = card
            cards.addWidget(card)
        step1_layout.addLayout(cards)
        # --- step 2
        step1_layout.addSpacing(6)
        step1_layout.addWidget(label("2  ·  WHERE ARE YOU COMING FROM?", "Eyebrow"))
        self.source = QComboBox()
        self.source.setAccessibleName("Where you are coming from")
        for key, text in wording.SOURCE_CHOICES:
            self.source.addItem(text, key)
        self.source.setCurrentIndex(self.source.count() - 1)
        self.source.setMaximumWidth(260)
        step1_layout.addWidget(self.source)
        # --- preview button
        self.preview_button = button("Preview the summary", "Primary")
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(lambda: self._prepare(force=False))
        row = QHBoxLayout()
        row.addWidget(self.preview_button)
        row.addStretch(1)
        self.spinner = Spinner()
        step1_layout.addSpacing(6)
        step1_layout.addLayout(row)
        step1_layout.addWidget(self.spinner)
        self.body.addWidget(self.step1)

        # --- preview
        self.preview_card = Card()
        self.preview_card.hide()
        self.preview_title = label("", "H2")
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMinimumHeight(280)
        self.preview_text.setAccessibleName("Summary preview")
        self.preview_size = label("", "Muted")
        self.preview_how = label("", "Small")
        self.preview_target = label("", "Muted")
        self.conflict_notice = Notice("")
        self.conflict_notice.hide()
        self.replace_button = button("Replace it")
        self.replace_button.hide()
        self.replace_button.clicked.connect(self._confirm_replace)
        self.write_button = button("Write the handoff", "Primary")
        self.write_button.clicked.connect(lambda: self._commit(force=False))
        cancel = button("Change something")
        cancel.clicked.connect(self._reset)
        for w in (
            self.preview_title,
            self.preview_text,
            self.preview_size,
            self.preview_how,
            self.preview_target,
        ):
            self.preview_card.body.addWidget(w)
        self.preview_card.body.addWidget(self.conflict_notice)
        buttons = QHBoxLayout()
        buttons.addWidget(self.write_button)
        buttons.addWidget(self.replace_button)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        self.preview_card.body.addSpacing(6)
        self.preview_card.body.addLayout(buttons)
        self.body.addWidget(self.preview_card)

        # --- done
        self.done_card = Card()
        self.done_card.hide()
        pic = pixmap_label(assets.illustration("done", 130))
        self.done_title = label("Handoff written", "H2")
        self.done_text = label("", "Muted")
        self.show_file = button("Show the file")
        self.show_file.clicked.connect(self._show_file)
        self.copy_button = button("Copy the summary")
        self.copy_button.clicked.connect(self._copy)
        again = button("Start another handoff")
        again.clicked.connect(self._reset)
        self.done_card.body.addWidget(pic)
        self.done_card.body.addWidget(self.done_title)
        self.done_card.body.addWidget(self.done_text)
        done_buttons = QHBoxLayout()
        for b in (self.show_file, self.copy_button, again):
            done_buttons.addWidget(b)
        done_buttons.addStretch(1)
        self.done_card.body.addSpacing(6)
        self.done_card.body.addLayout(done_buttons)
        self.body.addWidget(self.done_card)
        self.body.addStretch(1)

    @staticmethod
    def _column(holder: QWidget) -> QVBoxLayout:
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        return lay

    # ----- state ------------------------------------------------------------------------------------

    def refresh(self, snap: Snapshot | None) -> None:
        setup = self.needs_setup()
        if setup and self.setup_holder.layout() is None:
            lay = self._column(self.setup_holder)
            lay.addWidget(self.setup_prompt())
        self.setup_holder.setVisible(setup)
        self.step1.setVisible(not setup and self._committed is None and self._prepared is None)
        if (
            not setup
            and snap is not None
            and self.group.checkedButton() is None
            and snap.last_handoff
        ):
            last = str(snap.last_handoff.get("to", ""))
            if last in self.choice_cards:
                self.choice_cards[last].setChecked(True)
                self._choice_changed()

    def selected_agent(self) -> str | None:
        for key, card in self.choice_cards.items():
            if card.isChecked():
                return key
        return None

    def _choice_changed(self) -> None:
        self.preview_button.setEnabled(self.selected_agent() is not None)

    def _reset(self) -> None:
        self._prepared = None
        self._committed = None
        self.preview_card.hide()
        self.done_card.hide()
        self.conflict_notice.hide()
        self.replace_button.hide()
        self.step1.show()
        self.host.refresh()

    # ----- preview ------------------------------------------------------------------------------------

    def _prepare(self, *, force: bool) -> None:
        svc, target = self.service, self.selected_agent()
        if svc is None or target is None:
            return
        source = str(self.source.currentData())
        self.preview_button.setEnabled(False)
        self.spinner.show()

        def work(emit: object) -> PreparedHandoff:
            return svc.prepare_handoff(source, target, force=force, notify=emit)  # type: ignore[arg-type]

        def done(prepared: PreparedHandoff) -> None:
            self.spinner.hide()
            self.preview_button.setEnabled(True)
            self._prepared = prepared
            self._show_preview(prepared)

        def failed(exc: Exception) -> None:
            self.spinner.hide()
            self.preview_button.setEnabled(True)
            self.host.report_error(exc)

        self.host.runner.run(work, on_done=done, on_error=failed)

    def _show_preview(self, prepared: PreparedHandoff) -> None:
        handoff, adapter = prepared.handoff, prepared.adapter
        assert adapter is not None
        agent = wording.AGENT_LABELS.get(adapter.name, adapter.name)
        model = ""
        if handoff.method == "ollama" and self.service is not None:
            model = self.service.load_ai_settings().model
        failed = [
            (a.provider, a.outcome)
            for a in handoff.attempts
            if a.outcome not in ("ok", "retried", "truncated")
        ]
        self.preview_title.setText(f"Here's what {agent} will read")
        self.preview_text.setPlainText(handoff.payload.strip())
        self.preview_size.setText(
            wording.size_sentence(handoff.raw_tokens, handoff.compressed_tokens)
        )
        self.preview_how.setText(wording.how_written(handoff.method, failed, model))
        self.preview_target.setText(
            f"This will go into {adapter.relative_path} in your project folder."
        )
        self.conflict_notice.hide()
        self.replace_button.hide()
        self.write_button.setEnabled(True)
        self.step1.hide()
        self.done_card.hide()
        self.preview_card.show()

    # ----- writing ------------------------------------------------------------------------------------

    def _commit(self, *, force: bool) -> None:
        svc, prepared = self.service, self._prepared
        if svc is None or prepared is None:
            return
        if force and prepared.adapter is not None:
            prepared.adapter.force = True
        self.write_button.setEnabled(False)
        self.replace_button.setEnabled(False)

        def work(_emit: object) -> CommittedHandoff:
            return svc.commit_handoff(prepared)

        def done(committed: CommittedHandoff) -> None:
            self._committed = committed
            self._show_done(prepared, committed)
            self.host.refresh()

        def failed(exc: Exception) -> None:
            self.write_button.setEnabled(True)
            self.replace_button.setEnabled(True)
            if isinstance(exc, AdapterConflictError):
                name = prepared.adapter.relative_path if prepared.adapter else "That file"
                self.conflict_notice.set_text(
                    f"{name} already exists, and ContextBridge didn't create it. "
                    "Nothing was changed. You can replace it, or rename your file first."
                )
                self.conflict_notice.show()
                self.replace_button.show()
                return
            self.host.report_error(exc)

        self.host.runner.run(work, on_done=done, on_error=failed)

    def _confirm_replace(self) -> None:
        prepared = self._prepared
        name = prepared.adapter.relative_path if prepared and prepared.adapter else "that file"
        answer = QMessageBox.question(
            self,
            "Replace the file?",
            f"This replaces your existing {name} with the handoff summary.\n\nYour old version will be gone.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._commit(force=True)

    def _show_done(self, prepared: PreparedHandoff, committed: CommittedHandoff) -> None:
        adapter = prepared.adapter
        assert adapter is not None
        agent = wording.AGENT_LABELS.get(adapter.name, adapter.name)
        self.done_title.setText("Handoff written")
        self.done_text.setText(
            f"Your summary is now in {adapter.relative_path}. Open {agent} in this project and it will pick it up."
        )
        self.preview_card.hide()
        self.step1.hide()
        self.done_card.show()
        self.host.toast("Handoff written.")

    def _show_file(self) -> None:
        if self._committed is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._committed.written.parent)))

    def _copy(self) -> None:
        if self._prepared is not None:
            QGuiApplication.clipboard().setText(self._prepared.handoff.payload)
            self.host.toast("Copied.")
