"""The main window: a sidebar, the screens, and the glue between them."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from contextbridge.errors import ContextBridgeError
from contextbridge.gui import assets, theme
from contextbridge.gui.pages.base import ProjectPage
from contextbridge.gui.pages.changes import ChangesPage
from contextbridge.gui.pages.handoff import HandoffPage
from contextbridge.gui.pages.home import HomePage
from contextbridge.gui.pages.notes import NotesPage
from contextbridge.gui.pages.settings import SettingsPage
from contextbridge.gui.pages.welcome import WelcomePage
from contextbridge.gui.widgets import Toast, add_shortcut, button, label
from contextbridge.gui.workers import TaskRunner
from contextbridge.service import ProjectService, Snapshot, git_is_installed

log = logging.getLogger("contextbridge")

NAV: tuple[tuple[str, str], ...] = (
    ("home", "Home"),
    ("notes", "Notes"),
    ("changes", "Changes"),
    ("handoff", "Hand off"),
    ("settings", "Settings"),
)
MAX_RECENTS = 6
WELCOME_INDEX, SHELL_INDEX = 0, 1


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None, *, restore_last: bool = True) -> None:
        super().__init__()
        self.setWindowTitle("ContextBridge")
        self.setWindowIcon(assets.app_icon())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._activated_once = False
        self.setMinimumSize(880, 600)
        self.resize(1040, 720)
        self.settings = settings or QSettings("ContextBridge", "ContextBridge")
        self.service: ProjectService | None = None
        self.runner = TaskRunner(self)
        self.snapshot: Snapshot | None = None
        self._refresh_id = 0
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(350)
        self._refresh_timer.timeout.connect(self.refresh)

        self.root = QStackedWidget()
        self.setCentralWidget(self.root)
        self.welcome = WelcomePage(self)
        self.root.addWidget(self.welcome)
        self.root.addWidget(self._build_shell())
        self.toast_widget = Toast(self.root)

        self._shortcuts()
        self.welcome.set_recents(self.recent_projects())
        self.runner.run(
            lambda _emit: git_is_installed(),
            on_done=self.welcome.set_git_available,
            on_error=lambda _exc: None,
        )
        self.root.setCurrentIndex(WELCOME_INDEX)
        if restore_last:
            last = str(self.settings.value("lastProject", "") or "")
            if last and Path(last).is_dir():
                self.open_project(last)

    # ----- building the window ------------------------------------------------------------------

    def _build_shell(self) -> QWidget:
        shell = QWidget()
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_sidebar())
        self.pages = QStackedWidget()
        self.page_map: dict[str, ProjectPage] = {
            "home": HomePage(self),
            "notes": NotesPage(self),
            "changes": ChangesPage(self),
            "handoff": HandoffPage(self),
            "settings": SettingsPage(self),
        }
        for key, _text in NAV:
            self.pages.addWidget(self.page_map[key])
        row.addWidget(self.pages, 1)
        return shell

    def _build_sidebar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(theme.SIDEBAR_WIDTH)
        col = QVBoxLayout(bar)
        col.setContentsMargins(14, 24, 14, 18)
        col.setSpacing(4)

        brand_row = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(assets.app_icon().pixmap(34, 34))
        brand_row.addWidget(icon)
        brand_col = QVBoxLayout()
        brand_col.setSpacing(0)
        brand_col.addWidget(label("ContextBridge", "Brand", wrap=False))
        brand_row.addLayout(brand_col)
        brand_row.addStretch(1)
        col.addLayout(brand_row)
        col.addSpacing(20)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for index, (key, text) in enumerate(NAV, start=1):
            btn = QPushButton(f"  {text}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setIcon(assets.nav_icon(key))
            btn.setIconSize(btn.iconSize().expandedTo(btn.iconSize()))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn.setAccessibleName(text)
            btn.setToolTip(f"{text}  (Ctrl+{index})")
            btn.clicked.connect(lambda _c=False, k=key: self.go(k))
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            col.addWidget(btn)
        col.addStretch(1)

        project_heading = label("PROJECT", "Eyebrow")
        project_heading.setStyleSheet(f"color: {theme.ON_DARK_MUTED};")
        col.addWidget(project_heading)
        self.project_name = label("", "ProjectName", wrap=False)
        self.project_path = label("", "ProjectPath", wrap=False)
        col.addWidget(self.project_name)
        col.addWidget(self.project_path)
        change = button("Change folder…", "Link")
        change.setStyleSheet(
            f"QPushButton#Link {{ color: {theme.ON_DARK_MUTED}; }} QPushButton#Link:hover {{ color: {theme.ON_DARK}; }}"
        )
        change.clicked.connect(self.choose_project)
        col.addWidget(change, 0, Qt.AlignmentFlag.AlignLeft)
        return bar

    def _shortcuts(self) -> None:
        for index, (key, _text) in enumerate(NAV, start=1):
            add_shortcut(self, f"Ctrl+{index}", partial(self.go, key))
        add_shortcut(self, "Ctrl+O", self.choose_project)
        add_shortcut(self, "Ctrl+R", self.refresh)

    # ----- the Host interface used by the screens ------------------------------------------------

    def toast(self, text: str) -> None:
        self.toast_widget.show_message(text)

    def go(self, key: str) -> None:
        if key not in self.page_map:
            return
        self.nav_buttons[key].setChecked(True)
        self.pages.setCurrentWidget(self.page_map[key])

    def choose_project(self) -> None:
        start = str(self.service.root.parent) if self.service else str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Choose your project folder", start)
        if path:
            self.open_project(path)

    def open_project(self, path: str) -> None:
        folder = Path(path).expanduser()
        if not folder.is_dir():
            self._forget(str(folder))
            self.report_error(ContextBridgeErrorMessage(f"{folder} isn't available any more."))
            return
        self.service = ProjectService.for_path(folder)
        self.snapshot = None
        root = str(self.service.root)
        self._remember(root)
        self.settings.setValue("lastProject", root)
        self.project_name.setText(self.service.name)
        self.project_path.setText(self._shorten(root))
        self.project_path.setToolTip(root)
        self.root.setCurrentIndex(SHELL_INDEX)
        self.go("home")
        self.refresh()
        self._park_focus_soon()

    def refresh(self) -> None:
        """Re-read the project in the background and update every screen."""
        svc = self.service
        if svc is None:
            return
        if not svc.is_initialized():
            self.snapshot = None
            self._update_pages(None)
            return
        self._refresh_id += 1
        ticket = self._refresh_id

        def done(snap: Snapshot) -> None:
            if ticket == self._refresh_id and svc is self.service:  # ignore stale answers
                self.snapshot = snap
                self._update_pages(snap)

        self.runner.run(lambda _emit: svc.snapshot(), on_done=done, on_error=self.report_error)
        self._update_pages(self.snapshot)  # show what we have right away

    def _update_pages(self, snap: Snapshot | None) -> None:
        for page in self.page_map.values():
            page.refresh(snap)

    def report_error(self, exc: Exception) -> None:
        if isinstance(exc, ContextBridgeError):
            text = exc.message + (f"\n\n{exc.hint}" if exc.hint else "")
            title = "That didn't work"
        else:
            log.error("unexpected error in the app: %r", exc)
            text = f"Something unexpected happened ({type(exc).__name__}). The details were saved to ContextBridge's log."
            title = "Something went wrong"
        self.show_message(title, text)

    def show_message(self, title: str, text: str) -> None:
        """Show a friendly dialog. (A separate method so tests can replace it.)"""
        QMessageBox.information(self, title, text)

    # ----- remembering projects --------------------------------------------------------------------

    def recent_projects(self) -> list[str]:
        raw = self.settings.value("recentProjects", []) or []
        items = (
            [raw] if isinstance(raw, str) else (list(raw) if isinstance(raw, list | tuple) else [])
        )
        return [p for p in items if Path(str(p)).is_dir()][:MAX_RECENTS]

    def _remember(self, path: str) -> None:
        items = [p for p in self.recent_projects() if p != path]
        self.settings.setValue("recentProjects", [path, *items][:MAX_RECENTS])
        self.welcome.set_recents(self.recent_projects())

    def _forget(self, path: str) -> None:
        self.settings.setValue("recentProjects", [p for p in self.recent_projects() if p != path])
        if str(self.settings.value("lastProject", "")) == path:
            self.settings.remove("lastProject")
        self.welcome.set_recents(self.recent_projects())

    @staticmethod
    def _shorten(path: str, limit: int = 26) -> str:
        home = str(Path.home())
        text = "~" + path[len(home) :] if path.startswith(home) else path
        return text if len(text) <= limit else "…" + text[-(limit - 1) :]

    # ----- window events ------------------------------------------------------------------------------

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            if not self._activated_once:
                self._activated_once = True
                self._park_focus_soon()
            if self.service:
                self._refresh_timer.start()  # pick up commits made in other tools

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if self.toast_widget.isVisible():
            self.toast_widget.show_message(self.toast_widget.text(), 1200)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._park_focus_soon()

    def _park_focus_soon(self) -> None:
        # Qt hands focus to the first button whenever a screen appears; a ring on launch looks
        # like something is already selected. Park it on the window (Tab still reaches everything).
        QTimer.singleShot(0, self._park_focus)

    def _park_focus(self) -> None:
        widget = QApplication.focusWidget()
        if isinstance(widget, QPushButton):
            widget.clearFocus()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.settings.setValue("geometry", self.saveGeometry())
        self.runner.wait(3000)
        super().closeEvent(event)


class ContextBridgeErrorMessage(ContextBridgeError):
    """A plain message for the dialog (no exit code semantics in the app)."""
