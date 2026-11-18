"""Native diagram rendering, independent of graph discovery and project operations."""

from __future__ import annotations

from collections.abc import Callable
from html import escape
from math import atan2, cos, pi, sin
from typing import Any

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGraphicsItem,
    QGraphicsProxyWidget,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from contextbridge.clipboard import copy_text
from contextbridge.errors import ContextBridgeError
from contextbridge.gui import theme
from contextbridge.gui.widgets import button, label
from contextbridge.project_map import ProjectMap, export_mermaid, graph_view

GraphLoader = Callable[[str, str | None, int, Callable[[dict[str, Any]], None]], None]


class DiagramRenderer:
    """Only positions and paints a bounded graph JSON view; replace this for a new style."""

    def __init__(self, parent: QWidget, selected: Callable[[dict[str, Any]], None]) -> None:
        self.canvas = QGraphicsView(parent)
        self.canvas.setBackgroundBrush(QColor(theme.BG))
        self.canvas.setStyleSheet(
            f"QGraphicsView {{ background: {theme.BG}; border: 1px solid {theme.BORDER}; }}"
        )
        self.canvas.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.canvas.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.canvas.setMinimumHeight(430)
        self.canvas.setAccessibleName("Interactive project diagram")
        self.scene = QGraphicsScene(parent)
        self.scene.setBackgroundBrush(QColor(theme.BG))
        self.canvas.setScene(self.scene)
        self.items: dict[str, QGraphicsRectItem] = {}
        self.arrows: list[tuple[str, str, QGraphicsItem]] = []
        self.nodes: dict[str, dict[str, Any]] = {}
        self.selected = selected
        self.flow = False
        self.request_explain: Callable[[dict[str, Any]], None] | None = None
        self.scene.selectionChanged.connect(self._selection)

    def fit(self) -> None:
        self.canvas.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        if self.canvas.transform().m11() < 0.85:
            self.canvas.resetTransform()
            self.canvas.scale(0.85, 0.85)
        self.canvas.horizontalScrollBar().setValue(self.canvas.horizontalScrollBar().minimum())
        self.canvas.verticalScrollBar().setValue(self.canvas.verticalScrollBar().minimum())

    def render(self, data: dict[str, Any]) -> None:
        self.items, self.arrows, self.nodes = {}, [], {}
        self.scene.clear()
        self.flow = data["view"] in {"overview", "flow"}
        self.nodes = {n["id"]: n for n in data["nodes"]}
        row_y = row_height = 0.0
        for index, node in enumerate(data["nodes"]):
            x, y = (index % 2) * 370, row_y
            item = self.scene.addRect(
                x, y, 340, 250, QPen(QColor(theme.BORDER), 2), QColor(theme.SURFACE)
            )
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
            item.setData(0, node["id"])
            title = node["label"]
            ai = node.get("ai_explanation")
            description = ai["text"] if ai else node["purpose"]
            written = bool(ai) or node.get("purpose_source") == "AI-written"
            count = node.get("file_count", 1)
            header = self.scene.addText("")
            header.setHtml(
                f'<span style="color:{theme.TEXT}; font-size:18px; font-weight:600">{escape(title[:90])}</span>'
            )
            header.setTextWidth(310)
            header.setPos(x + 12, y + 10)
            header.setParentItem(item)
            body = self.scene.addText("")
            prefix = "AI-written: " if written else ""
            body.setHtml(
                f'<span style="color:{theme.TEXT}; font-size:15px">{escape(prefix + description[:210])}</span>'
            )
            body.setTextWidth(310)
            body.setPos(x + 12, y + 18 + header.boundingRect().height())
            body.setParentItem(item)
            coverage = (
                f" • {node.get('explained_files', 0)} explained" if node["kind"] == "folder" else ""
            )
            footer = self.scene.addText(f"{count} file{'s' if count != 1 else ''}" + coverage)
            footer.setDefaultTextColor(QColor(theme.TEXT_MUTED))
            footer_y = max(y + 120, body.pos().y() + body.boundingRect().height() + 8)
            footer.setPos(x + 12, footer_y)
            footer.setParentItem(item)
            for child in (header, body, footer):
                child.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            # The explanation lives inside the box; no tooltip obscures its title.
            extra_height = footer.boundingRect().height()
            if description in {"Not explained yet", "No description yet"} and self.request_explain:
                control = button("Explain")
                control.setStyleSheet("padding: 4px 8px; font-size: 13px;")
                control.setFixedSize(85, 32)
                proxy = QGraphicsProxyWidget(item)
                proxy.setWidget(control)
                proxy.setPos(x + 240, footer_y)
                control.clicked.connect(lambda _checked=False, n=node: self.request_explain(n))
                extra_height = max(extra_height, 32)
            height = footer_y - y + extra_height + 10
            item.setRect(x, y, 340, height)
            row_height = max(row_height, height)
            if index % 2:
                row_y += row_height + 40
                row_height = 0
            self.items[node["id"]] = item
        for edge in data["edges"]:
            a, b = edge["source"], edge["target"]
            ra, rb = self.items[a].rect(), self.items[b].rect()
            start = QPointF(ra.center().x(), ra.bottom())
            end = QPointF(rb.left(), rb.center().y())
            path = QPainterPath(start)
            lane, gutter = ra.bottom() + 18, rb.left() - 15
            path.lineTo(start.x(), lane)
            path.lineTo(gutter, lane)
            path.lineTo(gutter, end.y())
            path.lineTo(end)
            line = self.scene.addPath(path, QPen(QColor(theme.PRIMARY), 2))
            line.setToolTip(f"{self.nodes[a]['label']} {edge['kind']} {self.nodes[b]['label']}")
            line.setZValue(-1)
            angle = atan2(0, 1)
            polygon = QPolygonF(
                [
                    end,
                    end - QPointF(10 * cos(angle - pi / 6), 10 * sin(angle - pi / 6)),
                    end - QPointF(10 * cos(angle + pi / 6), 10 * sin(angle + pi / 6)),
                ]
            )
            head = self.scene.addPolygon(
                polygon, QPen(QColor(theme.PRIMARY)), QColor(theme.PRIMARY)
            )
            self.arrows.extend([(a, b, line), (a, b, head)])
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20))
        if self.items:
            next(iter(self.items.values())).setSelected(True)
        self.fit()

    def _selection(self) -> None:
        if not isValid(self.scene):
            return
        selection = self.scene.selectedItems()
        if not selection:
            return
        selected = str(selection[0].data(0))
        if selected not in self.nodes:
            return
        self.selected(self.nodes[selected])
        for key, box in self.items.items():
            box.setBrush(QColor(theme.SURFACE_ALT if key == selected else theme.SURFACE))
            box.setPen(
                QPen(
                    QColor("#632626" if key == selected else "#A08C7E"),
                    3 if key == selected else 1.5,
                )
            )
        for a, b, arrow in self.arrows:
            arrow.setVisible(self.flow or selected in {a, b})


class DiagramPanel(QWidget):
    def __init__(self, *, allow_expand: bool = True) -> None:
        super().__init__()
        self.limit = 12 if allow_expand else 24
        self.load_graph: GraphLoader | None = None
        self.explain_file: Callable[[str, Callable[[], None]], None] | None = None
        self.explain_component: Callable[[str, Callable[[], None]], None] | None = None
        self._ticket = 0
        self._map: ProjectMap | None = None
        self.data = graph_view(ProjectMap({}, [], 0, 0, False))
        self.selected_node: dict[str, Any] | None = None
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["Project overview", "Inside one file", "How a request travels"])
        self.mode.currentIndexChanged.connect(self._change_mode)
        controls.addWidget(self.mode, 1)
        self.module = QComboBox()
        self.module.setAccessibleName("Diagram file or starting point")
        self.module.setMinimumContentsLength(15)
        self.module.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.module.currentIndexChanged.connect(self._request)
        layout.addLayout(controls)
        layout.addWidget(self.module)
        self.renderer = DiagramRenderer(self, self._selection)
        self.renderer.request_explain = self._explain_node
        self.canvas, self.scene = self.renderer.canvas, self.renderer.scene
        for title, action in (
            ("+", lambda: self.canvas.scale(1.2, 1.2)),
            ("-", lambda: self.canvas.scale(1 / 1.2, 1 / 1.2)),
            ("Fit", self.renderer.fit),
        ):
            control = button(title)
            control.setMinimumWidth(76 if title == "Fit" else 52)
            control.setStyleSheet("padding: 6px 10px;")
            control.clicked.connect(action)
            controls.addWidget(control)
        if allow_expand:
            expand = button("Expand")
            expand.clicked.connect(self._expand)
            controls.addWidget(expand)
        self.caption = label("Refresh the guide to explore this project.", "Muted")
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        self.support = label("", "Muted")
        self.support.setWordWrap(True)
        layout.addWidget(self.support)
        layout.addWidget(self.canvas)
        actions = QHBoxLayout()
        self.open_part = button("Open this part")
        self.open_part.clicked.connect(self._open_part)
        actions.addWidget(self.open_part)
        self.explain_button = button("Explain this file")
        self.explain_button.clicked.connect(self._explain)
        self.explain_button.setEnabled(False)
        actions.addWidget(self.explain_button)
        self.copy_button = button("Copy as Mermaid")
        self.copy_button.clicked.connect(self._copy)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Selected part explanation")
        self.details.setMinimumHeight(140)
        self.details.setMaximumHeight(240)
        layout.addWidget(self.details)
        self._options()
        self.set_graph(self.data)

    @property
    def items(self) -> dict[str, QGraphicsRectItem]:
        return self.renderer.items

    @property
    def arrows(self) -> list[tuple[str, str, QGraphicsItem]]:
        return self.renderer.arrows

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self.renderer.fit)

    def set_model(self, model: ProjectMap) -> None:
        """Compatibility adapter: graph discovery still happens in project_map.py."""
        self._map = model
        self._request()

    def clear(self) -> None:
        self._ticket += 1
        self._map = None
        self.set_graph(graph_view(ProjectMap({}, [], 0, 0, False)))

    def _options(self, preferred: str | None = None) -> None:
        current = preferred or self.module.currentData()
        key = "entrypoints" if self.mode.currentIndex() == 2 else "available_modules"
        options = self.data["meta"].get(key, [])
        if not options and self.mode.currentIndex() == 2:
            options = self.data["meta"].get("available_modules", [])
        self.module.blockSignals(True)
        self.module.clear()
        for path in options:
            self.module.addItem(path, path)
        index = self.module.findData(current)
        if index >= 0:
            self.module.setCurrentIndex(index)
        self.module.blockSignals(False)
        self.module.setVisible(self.mode.currentIndex() != 0)

    def _change_mode(self) -> None:
        self._options()
        self._request()

    def _request(self) -> None:
        view = ("overview", "module", "flow")[self.mode.currentIndex()]
        path = self.module.currentData() if view != "overview" else None
        self._ticket += 1
        ticket = self._ticket

        def done(data: dict[str, Any]) -> None:
            if isValid(self) and ticket == self._ticket:
                self.set_graph(data)

        if self.load_graph:
            self.copy_button.setEnabled(False)
            self.explain_button.setEnabled(False)
            self.open_part.setEnabled(False)
            self.caption.setText("Reading the project's connections…")
            self.load_graph(view, path, self.limit, done)
        elif self._map is not None:
            done(graph_view(self._map, view, path, self.limit))

    def set_graph(self, data: dict[str, Any]) -> None:
        self.data = data
        self.copy_button.setEnabled(bool(data["nodes"]))
        self._options(data.get("module"))
        self.selected_node = None
        self.open_part.setEnabled(False)
        meta = data["meta"]
        note = (
            meta.get("flow_notice", "")
            if data["view"] == "flow"
            else "Pick a part to see what it does and which files use it."
        )
        if not data["edges"]:
            note += " " + (
                meta.get("connections_notice") or "Connections not detected in this view."
            )
        self.caption.setText(
            f"{meta['shown_nodes']} shown, {meta['hidden_nodes']} hidden • {meta['scanned_files']} files read. "
            + note
            + " Some dynamic connections may be missing."
        )
        self.support.setText(
            "Imports parsed: "
            + meta.get("import_support", "")
            + "\nEntry points parsed: "
            + meta.get("entrypoint_support", "")
        )
        self.details.setPlainText(
            "No source files found. Choose a project with supported source files."
        )
        self.explain_button.setEnabled(False)
        self.renderer.render(data)

    def _selection(self, node: dict[str, Any]) -> None:
        self.selected_node = node
        ai = node.get("ai_explanation")
        text = [node["label"], "What it does: " + node["purpose"]]
        text.append(
            "AI-written: " + ai["text"] if ai else "No AI-written explanation cached for this part."
        )
        for title, values in (
            ("Files involved", node["related_files"]),
            ("Uses", node["depends_on"]),
            ("Used by", node["used_by"]),
        ):
            if values or title == "Files involved":
                text.append(title + ": " + ("; ".join(values[:12]) or "None identified."))
            if len(values) > 12:
                text.append(f"+{len(values) - 12} more")
        if not node["depends_on"] and not node["used_by"]:
            text.append("Connections not detected")
        self.explain_button.setText(
            "Explain component" if node["kind"] == "folder" else "Explain this file"
        )
        self.explain_button.setEnabled(
            (node["kind"] == "folder" and self.explain_component is not None)
            or (
                node["kind"] in {"module", "file", "function", "class"}
                and self.explain_file is not None
            )
        )
        if node.get("routes"):
            text.append("Routes: " + "; ".join(node["routes"]))
        self.details.setPlainText("\n\n".join(text))
        self.open_part.setEnabled(
            node["kind"] in {"folder", "module", "file"} and bool(node["related_files"])
        )

    def _open_part(self) -> None:
        if self.selected_node and self.selected_node["related_files"]:
            path = self.selected_node["related_files"][0]
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(1)
            self.mode.blockSignals(False)
            self._options(path)
            self._request()

    def _explain_node(self, node: dict[str, Any]) -> None:
        self._selection(node)
        self._explain()

    def _explain(self) -> None:
        if self.selected_node:
            handler = (
                self.explain_component
                if self.selected_node["kind"] == "folder"
                else self.explain_file
            )
            if handler:

                def refresh() -> None:
                    if isValid(self):
                        self._request()

                handler(self.selected_node["path"], refresh)

    def _copy(self) -> None:
        try:
            copy_text(export_mermaid(self.data))
            self.caption.setText(
                "Copied this diagram as Mermaid. Paste it into a Markdown mermaid block."
            )
        except ContextBridgeError as exc:
            self.caption.setText(str(exc))

    def _expand(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("ContextBridge · Project diagrams")
        dialog.resize(1200, 900)
        layout = QVBoxLayout(dialog)
        panel = DiagramPanel(allow_expand=False)
        panel.load_graph, panel._map = self.load_graph, self._map
        panel.explain_file = self.explain_file
        panel.explain_component = self.explain_component
        panel.mode.blockSignals(True)
        panel.mode.setCurrentIndex(self.mode.currentIndex())
        panel.mode.blockSignals(False)
        panel.set_graph(self.data)
        panel._request()
        layout.addWidget(panel)
        close = button("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()
        dialog.deleteLater()
