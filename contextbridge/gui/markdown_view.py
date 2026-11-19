"""Native Markdown reader; renders locally without executing HTML or fetching images."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QTextDocument
from PySide6.QtWidgets import QTextBrowser

from contextbridge.gui import theme


class MarkdownView(QTextBrowser):
    def __init__(self) -> None:
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setMinimumHeight(520)
        self.setFont(QFont("Sans Serif", 13))
        self.document().setDocumentMargin(24)
        self.document().setDefaultStyleSheet(
            f"body {{ color: {theme.TEXT}; }} "
            "h1 { font-size: 25pt; margin-bottom: 18px; } "
            "h2 { font-size: 19pt; margin-top: 24px; margin-bottom: 12px; } "
            "h3 { font-size: 15pt; margin-top: 18px; } "
            "p, li { margin-top: 6px; margin-bottom: 10px; } "
            f"pre, code {{ background-color: {theme.SURFACE_ALT}; font-family: monospace; }} "
            f"table {{ border-color: {theme.BORDER}; }} td, th {{ padding: 8px; }}"
        )
        self.setStyleSheet(
            f"QTextBrowser {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 10px; padding: 8px; }}"
        )

    def loadResource(self, resource_type: int, name: QUrl | str) -> Any:
        # Project Markdown is untrusted: do not fetch remote images or read arbitrary files.
        return None

    def set_markdown(self, markdown: str) -> None:
        self.document().setMarkdown(markdown, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
