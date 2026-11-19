"""Evidence-based project guide using documentation and recorded session facts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
)

from contextbridge.errors import UserError
from contextbridge.gui.diagrams import DiagramPanel
from contextbridge.gui.markdown_view import MarkdownView
from contextbridge.gui.pages.base import Host, ProjectPage
from contextbridge.gui.widgets import button, label
from contextbridge.service import Snapshot


class GuidePage(ProjectPage):
    def __init__(self, host: Host) -> None:
        super().__init__(host)
        self.heading(
            "Project guide",
            "Understand your project",
            "Read the docs, explore flowcharts, and see what to work on next.",
        )
        refresh = button("Refresh guide", "Primary")
        refresh.clicked.connect(self._load)
        tools = QHBoxLayout()
        tools.addWidget(refresh)
        self.explain_project_button = button("Explain project")
        self.explain_project_button.clicked.connect(lambda: self._explain_project())
        tools.addWidget(self.explain_project_button)
        open_doc = button("Open Markdown…")
        open_doc.clicked.connect(self._choose_document)
        tools.addWidget(open_doc)
        for text, direction in (("A-", -1), ("A+", 1)):
            zoom = button(text)
            zoom.setAccessibleName("Decrease text size" if direction < 0 else "Increase text size")
            zoom.setFixedWidth(52)
            zoom.setStyleSheet("padding: 8px 4px; font-size: 12pt;")
            zoom.clicked.connect(lambda _checked=False, d=direction: self._zoom(d))
            tools.addWidget(zoom)
        self.body.addLayout(tools)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("GuideContent")
        self.tabs.tabBar().hide()
        self.views: dict[str, MarkdownView] = {}
        for key in ("What it does", "Progress", "Next steps", "Suggestions"):
            view = MarkdownView()
            view.anchorClicked.connect(self._follow_link)
            view.setReadOnly(True)
            self.views[key] = view
            self.tabs.addTab(view, key)
        self.diagram = DiagramPanel()
        self.diagram.load_graph = self._graph
        self.diagram.explain_file = self._explain_file
        self.diagram.explain_component = lambda group, done: self._explain_project(group, done)
        self.tabs.addTab(self.diagram, "Flowcharts")
        self.tabs.setUsesScrollButtons(True)
        self.file_map = QTableWidget(0, 2)
        self.file_map.setHorizontalHeaderLabels(["Changed file", "Likely purpose (from its name)"])
        self.file_map.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_map.horizontalHeader().setStretchLastSection(True)
        self.file_map.setMinimumHeight(400)
        self.file_map.setAlternatingRowColors(True)
        self.file_map.verticalHeader().hide()
        self.tabs.addTab(self.file_map, "File explanations")
        # Two rows keep every section visible at the minimum window width.
        self.section_buttons = {}
        sections = QGridLayout()
        sections.setHorizontalSpacing(10)
        sections.setVerticalSpacing(10)
        self.section_group = QButtonGroup(self)
        self.section_group.setExclusive(True)
        choices = [
            ("What it does", 0),
            ("Flowcharts", 4),
            ("Progress", 1),
            ("Next steps", 2),
            ("File explanations", 5),
            ("Suggestions", 3),
        ]
        for position, (title, index) in enumerate(choices):
            control = button(title, "GuideSection")
            control.setCheckable(True)
            control.setAccessibleName(f"Project guide: {title}")
            control.clicked.connect(lambda _checked=False, i=index: self._select_section(i))
            self.section_group.addButton(control, index)
            self.section_buttons[title] = control
            sections.addWidget(control, position // 3, position % 3)
        self.tabs.currentChanged.connect(self._section_changed)
        self._section_changed(self.tabs.currentIndex())
        self.body.addLayout(sections)
        self.body.addWidget(self.tabs)
        self.status = label("Choose a project, then refresh the guide.", "Muted")
        self.body.addWidget(self.status)
        self._root: str | None = None
        self._document_path: Path | None = None
        self._read_ticket = 0

    def _select_section(self, index: int) -> None:
        self.tabs.setCurrentIndex(index)
        if self.tabs.currentWidget() is self.diagram:
            scroll = self.findChild(QScrollArea)
            if scroll is not None:
                QTimer.singleShot(0, lambda: scroll.ensureWidgetVisible(self.diagram, 0, 12))

    def _section_changed(self, index: int) -> None:
        control = self.section_group.button(index)
        if control is not None:
            control.setChecked(True)

    def _zoom(self, direction: int) -> None:
        for view in self.views.values():
            view.zoomIn(direction)

    def _choose_document(self) -> None:
        if self.service is None:
            return
        name, _ = QFileDialog.getOpenFileName(
            self, "Open project Markdown", str(self.service.root), "Markdown (*.md)"
        )
        if name:
            self._read_document(Path(name))

    def _follow_link(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
        elif not url.scheme() and url.path() and self._document_path:
            self._read_document(self._document_path.parent / url.path())
        elif not url.path() and url.fragment():
            self.views["What it does"].scrollToAnchor(url.fragment())

    def _read_document(self, path: Path, *, select: bool = True) -> None:
        svc = self.service
        if svc is None:
            return
        root = svc.root
        try:
            path = svc.document_path(path)
        except UserError as exc:
            self.status.setText(str(exc))
            return
        self._read_ticket += 1
        ticket = self._read_ticket
        self.status.setText("Rendering local documentation…")

        def done(text: str) -> None:
            if self.service is not svc or ticket != self._read_ticket:
                return
            self._document_path = path
            self.views["What it does"].set_markdown(text)
            if select:
                self.tabs.setCurrentWidget(self.views["What it does"])
            self.status.setText(
                f"Reading {path.relative_to(root)} • rendered locally • A- / A+ changes text size"
            )

        def failed(exc: Exception) -> None:
            if self.service is svc and ticket == self._read_ticket:
                self.status.setText(f"Could not read document: {exc}")

        self.host.runner.run(lambda _emit: svc.read_document(path), on_done=done, on_error=failed)

    def _load(self) -> None:
        svc = self.service
        if svc is None:
            return
        for name in ("README.md", "README.rst", "README.txt", "README"):
            path = svc.root / name
            if path.is_file():
                self._read_document(path, select=False)
                break
        else:
            self.views["What it does"].set_markdown(
                "## No README found\nUse **Open Markdown…** to choose project documentation."
            )

        self.diagram._request()
        self.host.refresh()

    def _explain_file(self, path: str, done: Any) -> None:
        svc = self.service
        if svc is None:
            return
        try:
            notice = svc.file_explanation_notice(path)
        except UserError as exc:
            self.status.setText(str(exc))
            return
        if (
            QMessageBox.question(
                self,
                "Explain this file · privacy",
                notice,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.diagram.explain_button.setEnabled(False)
        self.status.setText("Explaining this file…")

        def ready(_text: str) -> None:
            if self.service is svc:
                self.status.setText("AI-written explanation cached for this file.")
                done()

        def failed(exc: Exception) -> None:
            if self.service is svc:
                self.status.setText(str(exc))
                self.diagram.explain_button.setEnabled(True)

        self.host.runner.run(
            lambda emit: svc.explain_file(path, notify=emit),
            on_done=ready,
            on_error=failed,
            on_progress=lambda text: self.status.setText(text) if self.service is svc else None,
        )

    def _explain_project(self, component: str | None = None, done: Any = None) -> None:
        svc = self.service
        if svc is None:
            return
        try:
            notice = svc.project_explanation_notice(20, 40000)
        except UserError as exc:
            self.status.setText(str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Explain component" if component is not None else "Explain project")
        layout = QVBoxLayout(dialog)
        privacy = label(notice, "Muted")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        form = QFormLayout()
        requests = QSpinBox()
        requests.setObjectName("ExplanationRequestBudget")
        requests.setRange(1, 200)
        requests.setValue(20)
        tokens = QSpinBox()
        tokens.setObjectName("ExplanationTokenBudget")
        tokens.setRange(1000, 1000000)
        tokens.setSingleStep(1000)
        tokens.setValue(40000)
        form.addRow("Maximum requests (files, retries and folder summaries)", requests)
        form.addRow("Reserved tokens (estimated input + maximum output)", tokens)
        layout.addLayout(form)

        def update_notice() -> None:
            privacy.setText(svc.project_explanation_notice(requests.value(), tokens.value()))

        requests.valueChanged.connect(update_notice)
        tokens.valueChanged.connect(update_notice)
        note = label(
            "Files run in importance order. Cached files cost no requests. "
            "A rate limit stops the run; run this action again to resume.",
            "Muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        controls.button(QDialogButtonBox.StandardButton.Ok).setText("Explain")
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        dialog.resize(640, 400)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        budget_requests, budget_tokens = requests.value(), tokens.value()
        dialog.deleteLater()
        self.explain_project_button.setEnabled(False)
        self.diagram.explain_button.setEnabled(False)
        self.status.setText(
            f"Explaining • budget {budget_requests} requests / {budget_tokens:,} reserved tokens"
        )

        def ready(report: str) -> None:
            self.explain_project_button.setEnabled(True)
            if self.service is svc:
                self.status.setText(report)
                (done or self.diagram._request)()

        def failed(exc: Exception) -> None:
            self.explain_project_button.setEnabled(True)
            if self.service is svc:
                self.status.setText(str(exc))
                self.diagram.explain_button.setEnabled(True)

        self.host.runner.run(
            lambda emit: svc.explain_project(
                max_requests=budget_requests,
                token_budget=budget_tokens,
                component=component,
                notify=emit,
            ),
            on_done=ready,
            on_error=failed,
            on_progress=lambda text: self.status.setText(text) if self.service is svc else None,
        )

    def _graph(self, view: str, module: str | None, limit: int, done: Any) -> None:
        svc = self.service
        if svc is None:
            return

        def ready(data: dict[str, Any]) -> None:
            if self.service is svc:
                done(data)

        self.host.runner.run(
            lambda _emit: svc.project_graph(view, module=module, limit=limit),
            on_done=ready,
            on_error=self.host.report_error,
        )

    def refresh(self, snap: Snapshot | None) -> None:
        root = str(self.service.root) if self.service else None
        if root != self._root:
            self._root = root
            self._document_path = None
            self._read_ticket += 1
            self.diagram.clear()
            for view in self.views.values():
                view.clear()
            self.status.setText("Click Refresh guide to read this project's documentation.")
        self.file_map.setRowCount(0)
        svc = self.service
        if snap is None or svc is None:
            self.views["Progress"].set_markdown(
                "No recorded session yet. Set up the project in Home to collect changes and notes."
            )
            self.views["Next steps"].set_markdown(
                "Add To-do notes to record planned improvements. Plans will be shown here without guessing."
            )
            self.views["Suggestions"].set_markdown(
                "Suggested check: document the project's purpose, installation steps, and one example workflow."
            )
            return
        content = svc.guide_content(snap)
        self.file_map.setRowCount(len(content.files))
        for row, (path, purpose) in enumerate(content.files):
            self.file_map.setItem(row, 0, QTableWidgetItem(path))
            self.file_map.setItem(row, 1, QTableWidgetItem(purpose))
        self.views["Progress"].set_markdown(content.progress)
        self.views["Next steps"].set_markdown(content.next_steps)
        self.views["Suggestions"].set_markdown(content.suggestions)
