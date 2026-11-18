"""Chat transfer without a project or an AI service."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QLineEdit, QPlainTextEdit, QSpinBox

from contextbridge.gui.chat_pdf import save_chat_pdf
from contextbridge.gui.pages.base import Host, ProjectPage
from contextbridge.gui.widgets import button, label
from contextbridge.service import ChatService, ImportedChat, Snapshot


class ChatPage(ProjectPage):
    def __init__(self, host: Host) -> None:
        super().__init__(host)
        self.heading(
            "Chat bridge",
            "Continue with another chatbot",
            "Paste a conversation, keep the facts that matter, and copy a compact handoff. No account, project, or model needed.",
        )
        self.body.addWidget(label("Chat text — preserve speaker labels where possible"))
        self.transcript = QPlainTextEdit()
        self.transcript.setAccessibleName("Chat transcript")
        self.transcript.setMinimumHeight(160)
        self.body.addWidget(self.transcript)
        self.import_button = button("Import chat (PDF or text)…")
        self.import_button.clicked.connect(self._import)
        self.body.addWidget(self.import_button)
        self._import_ticket = 0
        self.transcript.textChanged.connect(self._invalidate_import)
        self.goal = QLineEdit()
        self.goal.setMaxLength(1000)
        self.goal.setPlaceholderText("What should the next chatbot help with?")
        self.goal.setAccessibleName("Goal or next question")
        self.body.addWidget(self.goal)
        self.pinned = QPlainTextEdit()
        self.pinned.setPlaceholderText(
            "Essential facts, decisions, preferences, or unresolved questions to keep"
        )
        self.pinned.setAccessibleName("Essential facts to keep")
        self.pinned.setMaximumHeight(90)
        self.body.addWidget(self.pinned)
        self.budget = QSpinBox()
        self.budget.setRange(2000, 12000)
        self.budget.setSingleStep(1000)
        self.budget.setValue(6000)
        self.budget.setSuffix(" characters maximum")
        self.budget.setAccessibleName("Handoff size limit")
        self.body.addWidget(self.budget)
        make = button("Prepare handoff", "Primary")
        make.clicked.connect(self._prepare)
        self.body.addWidget(make)
        self.status = label(
            "Nothing is sent anywhere. Imported text stays in memory until you clear it or close the app. Review for personal details before sharing.",
            "Muted",
        )
        self.body.addWidget(self.status)
        self.preview = QPlainTextEdit()
        self.preview.setAccessibleName("Editable handoff preview")
        self.preview.setMinimumHeight(180)
        self.body.addWidget(self.preview)
        copy = button("Copy handoff")
        copy.clicked.connect(self._copy)
        self.body.addWidget(copy)
        save = button("Save handoff…")
        save.clicked.connect(self._save)
        self.body.addWidget(save)
        pdf = button("Save PDF…")
        pdf.clicked.connect(self._save_pdf)
        self.body.addWidget(pdf)
        clear = button("Clear chat and preview")
        clear.clicked.connect(self._clear)
        self.body.addWidget(clear)

    def refresh(self, snap: Snapshot | None) -> None:
        pass

    def _prepare(self) -> None:
        try:
            packet = ChatService.prepare(
                self.transcript.toPlainText(),
                self.goal.text(),
                self.pinned.toPlainText(),
                self.budget.value(),
            )
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.preview.setPlainText(packet.text)
        self.status.setText(
            f"{packet.source_chars:,} source characters → {len(packet.text):,} handoff characters. {packet.omitted_chars:,} earlier characters omitted. Approximately {len(packet.text) // 4:,} tokens (rough character estimate; not a credit estimate). Review the excerpt and add any missing facts."
        )

    def _invalidate_import(self) -> None:
        self._import_ticket += 1

    def _import(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Import chat", "", "Chat files (*.pdf *.txt *.md);;PDF (*.pdf);;Text (*.txt *.md)"
        )
        if not name:
            return
        self._import_ticket += 1
        ticket = self._import_ticket
        self.import_button.setEnabled(False)
        self.status.setText("Reading the file locally… You can keep using the app.")

        def done(result: ImportedChat) -> None:
            self.import_button.setEnabled(True)
            if ticket != self._import_ticket:
                return
            self.transcript.setPlainText(result.text)
            self.preview.clear()
            self.status.setText(result.notice)

        def failed(exc: Exception) -> None:
            self.import_button.setEnabled(True)
            if ticket != self._import_ticket:
                return
            self.status.setText(
                str(exc)
                if isinstance(exc, (OSError, UnicodeError, ValueError))
                else "Could not import this file. Try a text export instead."
            )

        self.host.runner.run(
            lambda _emit: ChatService.import_file(Path(name)), on_done=done, on_error=failed
        )

    def _save_pdf(self) -> None:
        text = self.preview.toPlainText()
        if not text.strip():
            self.status.setText("Prepare a handoff before saving a PDF.")
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "Save handoff as PDF", "chat-handoff.pdf", "PDF (*.pdf)"
        )
        if not name:
            return
        path = Path(name)
        if path.suffix.lower() != ".pdf":
            path = path.with_name(path.name + ".pdf")
        try:
            save_chat_pdf(path, text)
        except (OSError, ValueError) as exc:
            self.status.setText(f"Could not save PDF: {exc}")
            return
        self.status.setText(f"PDF saved: {path.name}. It contains the reviewed handoff preview.")
        self.host.toast("Handoff saved as PDF.")

    def _copy(self) -> None:
        if self.preview.toPlainText().strip():
            QGuiApplication.clipboard().setText(self.preview.toPlainText())
            self.host.toast("Copied. Paste into your new chatbot conversation.")

    def _save(self) -> None:
        if not self.preview.toPlainText().strip():
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "Save handoff", "chat-handoff.md", "Markdown (*.md)"
        )
        if name:
            try:
                Path(name).write_text(self.preview.toPlainText(), encoding="utf-8")
            except OSError as exc:
                self.status.setText(str(exc))

    def _clear(self) -> None:
        self._invalidate_import()
        for field in (self.transcript, self.pinned, self.preview, self.goal):
            field.clear()
        self.status.setText(
            "Chat cleared from this screen. Previously saved files and clipboard contents are unchanged."
        )
