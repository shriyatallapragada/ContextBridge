"""Paginated local PDF export using the GUI's existing font and print engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QIODevice, QMarginsF, QSaveFile, QUrl
from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPdfWriter, QTextDocument, QTextOption

from contextbridge.chat_bridge import MAX_CHAT_CHARS


class _ExportDocument(QTextDocument):
    def loadResource(self, resource_type: int, name: QUrl | str) -> Any:
        # Chat text must never trigger local-file reads or remote image requests.
        return None


def save_chat_pdf(path: Path, markdown: str) -> None:
    """Export the reviewed preview. Must run on the GUI thread with a QApplication."""
    if not markdown.strip():
        raise ValueError("Prepare a handoff before saving a PDF.")
    if len(markdown) > MAX_CHAT_CHARS:
        raise ValueError("Shorten the preview to 100,000 characters before exporting.")
    target = QSaveFile(str(path))
    if not target.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(target.errorString())
    try:
        writer = QPdfWriter(target)
        writer.setTitle("ContextBridge conversation handoff")
        writer.setCreator("ContextBridge")
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageMargins(QMarginsF(18, 18, 18, 18), QPageLayout.Unit.Millimeter)
        writer.setResolution(96)
        document = _ExportDocument()
        document.setDefaultFont(QFont("Sans Serif", 11))
        document.setDocumentMargin(8)
        document.setDefaultStyleSheet(
            "h1 { color: #632626; font-size: 22pt; margin-bottom: 16px; } "
            "h2 { color: #632626; font-size: 15pt; margin-top: 18px; } "
            "p, li { margin-top: 6px; margin-bottom: 8px; } "
            "pre, code { font-family: monospace; font-size: 10pt; }"
        )
        option = document.defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        document.setMarkdown(
            markdown,
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QTextDocument.MarkdownFeature.MarkdownNoHTML,
        )
        document.print_(writer)
        del writer  # finalize PDF data before committing the atomic save
        if not target.commit():
            raise OSError(target.errorString())
    except Exception:
        target.cancelWriting()
        raise
