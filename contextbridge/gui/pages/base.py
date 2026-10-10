"""What every screen needs from the main window, and a base class for project screens."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtWidgets import QWidget

from contextbridge.gui.widgets import EmptyState, Page, button
from contextbridge.gui.workers import TaskRunner
from contextbridge.service import ProjectService, Snapshot


class Host(Protocol):
    """The slice of the main window that screens use (keeps screens easy to test)."""

    service: ProjectService | None
    runner: TaskRunner

    def toast(self, text: str) -> None: ...
    def refresh(self) -> None: ...
    def go(self, key: str) -> None: ...
    def report_error(self, exc: Exception) -> None: ...
    def choose_project(self) -> None: ...
    def open_project(self, path: str) -> None: ...


class ProjectPage(Page):
    """A screen that shows a project. Subclasses implement :meth:`refresh`."""

    def __init__(self, host: Host, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.host = host

    def refresh(self, snap: Snapshot | None) -> None:
        raise NotImplementedError

    @property
    def service(self) -> ProjectService | None:
        return self.host.service

    def needs_setup(self) -> bool:
        svc = self.host.service
        return svc is None or not svc.is_initialized()

    def setup_prompt(self) -> EmptyState:
        """Shown instead of content until the project has been set up."""
        action = button("Go to Home to set up", "Primary")
        action.clicked.connect(lambda: self.host.go("home"))
        return EmptyState(
            "welcome",
            "Let's set this project up first",
            "It only takes one click, and it doesn't change any of your files.",
            action,
        )
