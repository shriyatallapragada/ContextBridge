"""Notes: write down what you decided, what's stuck, what's next."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from contextbridge.gui import wording
from contextbridge.gui.pages.base import Host, ProjectPage
from contextbridge.gui.widgets import (
    Card,
    ChipRow,
    EmptyState,
    Notice,
    add_shortcut,
    button,
    clear_layout,
    label,
)
from contextbridge.service import NoteResult, Snapshot
from contextbridge.storage.db import Decision


class NotesPage(ProjectPage):
    def __init__(self, host: Host, parent: QWidget | None = None) -> None:
        super().__init__(host, parent)
        self._decisions: list[Decision] = []
        self.heading(
            "Notes", "What's on your mind?", "Jot it down. Your next assistant will thank you."
        )

        self.editor_card = Card()
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "For example: We decided to store prices in cents, because decimals caused rounding bugs."
        )
        self.editor.setMinimumHeight(110)
        self.editor.setAccessibleName("Write a note")
        self.chips = ChipRow([(k.key, k.label, k.hint) for k in wording.NOTE_KINDS])
        self.chips.set_value("note")
        self.save = button("Save note", "Primary")
        self.save.setToolTip("Save (Ctrl+Enter)")
        self.save.clicked.connect(self._save)
        add_shortcut(self.editor, "Ctrl+Return", self._save)
        add_shortcut(self.editor, "Ctrl+Enter", self._save)
        self.editor_card.body.addWidget(self.editor)
        self.editor_card.body.addSpacing(4)
        self.editor_card.body.addWidget(label("WHAT KIND OF NOTE?", "Eyebrow"))
        self.editor_card.body.addWidget(self.chips)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.save)
        self.editor_card.body.addLayout(row)
        self.body.addWidget(self.editor_card)

        self.redaction_notice = Notice("")
        self.redaction_notice.hide()
        self.body.addWidget(self.redaction_notice)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search your notes…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search notes")
        self.search.textChanged.connect(lambda _t: self._render())
        self.body.addWidget(self.search)
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(10)
        self.body.addLayout(self.list_box)
        self.body.addStretch(1)

    # ----- saving ---------------------------------------------------------------------------

    def _save(self) -> None:
        svc = self.service
        text = self.editor.toPlainText().strip()
        if svc is None or not text or self.needs_setup():
            if not text:
                self.host.toast("Write something first.")
            return
        kind = self.chips.value() or "note"
        self.save.setEnabled(False)

        def work(_emit: object) -> NoteResult:
            return svc.add_note(text, kind)

        def done(result: NoteResult) -> None:
            self.save.setEnabled(True)
            self.editor.clear()
            hidden = result.redactions.total
            if hidden:
                self.redaction_notice.set_text(
                    f"For your safety, ContextBridge hid {wording.plural(hidden, 'value')} that looked like "
                    "a password or key before saving."
                )
                self.redaction_notice.show()
            else:
                self.redaction_notice.hide()
            self.host.toast("Saved.")
            self.host.refresh()

        def failed(exc: Exception) -> None:
            self.save.setEnabled(True)
            self.host.report_error(exc)

        self.host.runner.run(work, on_done=done, on_error=failed)

    # ----- the list --------------------------------------------------------------------------

    def refresh(self, snap: Snapshot | None) -> None:
        self._decisions = list(snap.context.decisions) if snap else []
        self.editor_card.setVisible(not self.needs_setup())
        self.search.setVisible(not self.needs_setup() and bool(self._decisions))
        self._render()

    def _render(self) -> None:
        clear_layout(self.list_box)
        if self.needs_setup():
            self.list_box.addWidget(self.setup_prompt())
            return
        query = self.search.text().strip().lower()
        shown = (
            [d for d in self._decisions if query in d.content.lower()] if query else self._decisions
        )
        if not self._decisions:
            self.list_box.addWidget(
                EmptyState(
                    "notes",
                    "No notes yet",
                    "Decisions and to-dos you save here show up in your handoff.",
                )
            )
            return
        if not shown:
            self.list_box.addWidget(label("No notes match that search.", "Muted"))
            return
        for d in shown:
            card = Card()
            top = QHBoxLayout()
            tag = label(wording.NOTE_LABELS.get(d.tag, d.tag.title()), "Tag", wrap=False)
            tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            top.addWidget(tag)
            top.addWidget(label(wording.when(d.created_at), "Small"))
            top.addStretch(1)
            card.body.addLayout(top)
            card.body.addWidget(label(d.content))
            self.list_box.addWidget(card)
