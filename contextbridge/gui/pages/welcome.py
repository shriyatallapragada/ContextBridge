"""The first screen: pick a project folder."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from contextbridge.gui import assets
from contextbridge.gui.pages.base import Host
from contextbridge.gui.widgets import Notice, button, clear_layout, label, pixmap_label


class WelcomePage(QWidget):
    def __init__(self, host: Host, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.host = host
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.setSpacing(14)
        outer.setContentsMargins(40, 40, 40, 40)

        pic = pixmap_label(assets.illustration("welcome", 190))
        pic.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title = label("ContextBridge", "H1")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        intro = label(
            "Keep your AI coding assistants on the same page. ContextBridge remembers what "
            "you're working on, so you can switch tools without starting over.",
            "Muted",
        )
        intro.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        intro.setMinimumWidth(460)
        intro.setMaximumWidth(520)
        self.choose = button("Choose a project folder…", "Primary")
        self.choose.setAccessibleDescription("Pick the folder of the project you are working on")
        self.choose.clicked.connect(host.choose_project)

        outer.addWidget(pic, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(title)
        outer.addWidget(intro, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addSpacing(8)
        outer.addWidget(self.choose, 0, Qt.AlignmentFlag.AlignHCenter)

        self.recent_box = QVBoxLayout()
        self.recent_box.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addLayout(self.recent_box)
        self.git_notice = Notice(
            "Git isn't installed on this computer yet. ContextBridge can still keep notes, "
            "but it needs Git to notice your code changes. You can get it free at git-scm.com."
        )
        self.git_notice.setMaximumWidth(520)
        self.git_notice.hide()
        outer.addWidget(self.git_notice, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_git_available(self, ok: bool) -> None:
        self.git_notice.setVisible(not ok)

    def set_recents(self, paths: list[str]) -> None:
        clear_layout(self.recent_box)
        if not paths:
            return
        heading = label("RECENT PROJECTS", "Eyebrow")
        heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.recent_box.addSpacing(10)
        self.recent_box.addWidget(heading)
        for path in paths:
            link = button(f"{Path(path).name}   ·   {Path(path).parent}", "Link")
            link.clicked.connect(lambda _c=False, p=path: self.host.open_project(p))
            self.recent_box.addWidget(link, 0, Qt.AlignmentFlag.AlignHCenter)
