"""Native, offline diagrams with pan, zoom, and selectable component explanations."""

from __future__ import annotations

from itertools import pairwise
from math import atan2, cos, pi, sin

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from contextbridge.gui import theme
from contextbridge.gui.widgets import button, label
from contextbridge.project_map import ProjectMap, visible_map

WORKFLOW = {
    "1. Capture": ["ContextBridge reads project changes and the notes you record."],
    "2. Store locally": ["Captured project context lives in the project's .context folder."],
    "3. Prepare": ["Create a bounded handoff using a template or optional AI."],
    "4. Review": ["Preview the handoff and check important facts before writing it."],
    "5. Continue": ["Write the handoff to the chosen agent's file, then continue there."],
}


class DiagramPanel(QWidget):
    def __init__(self, *, allow_expand: bool = True) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.setMinimumContentsLength(12)
        self.mode.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.mode.addItems(["Architecture: project components", "Workflow: how a handoff works"])
        self.mode.currentIndexChanged.connect(self._draw)
        controls.addWidget(self.mode, 1)
        self.canvas = QGraphicsView()
        self.canvas.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.canvas.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.canvas.setMinimumHeight(430)
        self.canvas.setAccessibleName("Interactive project diagram")
        for title, action in (
            ("+", lambda: self.canvas.scale(1.2, 1.2)),
            ("-", lambda: self.canvas.scale(1 / 1.2, 1 / 1.2)),
            ("Fit", self.fit),
        ):
            control = button(title)
            control.setFixedWidth(52 if title == "Fit" else 44)
            control.setStyleSheet("padding: 8px 4px; font-size: 12pt;")
            control.clicked.connect(action)
            controls.addWidget(control)
        if allow_expand:
            expand = button("Expand")
            expand.setFixedWidth(88)
            expand.setStyleSheet("padding: 8px 6px; font-size: 12pt;")
            expand.setAccessibleName("Open larger flowchart window")
            expand.clicked.connect(self._expand)
            controls.addWidget(expand)
        layout.addLayout(controls)
        self.caption = label("Refresh the guide to map the selected project.", "Muted")
        layout.addWidget(self.caption)
        layout.addWidget(self.canvas)
        self.details = label("Select a component to see its files and direct imports. Drag to pan.")
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.details)
        self.model = ProjectMap({}, [], 0, 0, False)
        self.scene = QGraphicsScene(self)
        self.canvas.setScene(self.scene)
        self.scene.selectionChanged.connect(self._selection)
        self.items: dict[str, QGraphicsRectItem] = {}
        self.arrows: list[tuple[str, str, QGraphicsItem]] = []
        self.mode.setCurrentIndex(1)
        self._draw()

    def _expand(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("ContextBridge · Flowcharts")
        dialog.resize(1100, 800)
        layout = QVBoxLayout(dialog)
        panel = DiagramPanel(allow_expand=False)
        panel.set_model(self.model)
        panel.mode.setCurrentIndex(self.mode.currentIndex())
        layout.addWidget(panel)
        close = button("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()
        dialog.deleteLater()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self.fit)

    def set_model(self, model: ProjectMap) -> None:
        self.model = visible_map(model, limit=9)
        self._draw()

    def fit(self) -> None:
        self.canvas.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _draw(self) -> None:
        self.items = {}
        self.arrows = []
        self.scene.clear()
        workflow = self.mode.currentIndex() == 1
        nodes = WORKFLOW if workflow else self.model.nodes
        names = list(nodes)
        edges = list(pairwise(names)) if workflow else self.model.edges
        self.caption.setText(
            "ContextBridge handoff process. Click any step to learn more."
            if workflow
            else f"Static Python imports • {self.model.scanned} files scanned • {self.model.skipped} skipped. "
            + ("Limited view. " if self.model.limited else "")
            + "Arrows mean 'imports'. Select a box to show its outgoing connections. Dynamic imports and other languages are not mapped."
        )
        if not nodes:
            self.details.setText(
                "No Python components mapped. Refresh the guide. For other languages, use the rendered architecture document; no dependencies are guessed."
            )
            return
        for i, name in enumerate(names):
            column = 2 - i % 3 if workflow and (i // 3) % 2 else i % 3
            x, y = column * 280, (i // 3) * 155
            item = self.scene.addRect(
                x, y, 250, 115, QPen(QColor(theme.BORDER), 2), QColor(theme.SURFACE)
            )
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            item.setData(0, name)
            title = name if workflow else name.split("/", 1)[-1]
            text = self.scene.addText(title.replace("/", "/\u200b"), QFont("Sans Serif", 18))
            text.setDefaultTextColor(QColor(theme.TEXT))
            text.setTextWidth(220)
            text.setPos(x + 12, y + 12)
            text.setParentItem(item)
            text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.items[name] = item
        for a, b in edges:
            ra, rb = self.items[a].rect(), self.items[b].rect()
            if ra.top() == rb.top():
                right = rb.center().x() > ra.center().x()
                start = QPointF(ra.right() if right else ra.left(), ra.center().y())
                end = QPointF(rb.left() if right else rb.right(), rb.center().y())
                offset = QPointF(25 if right else -25, 0)
            else:
                down = rb.center().y() > ra.center().y()
                start = QPointF(ra.center().x(), ra.bottom() if down else ra.top())
                end = QPointF(rb.center().x(), rb.top() if down else rb.bottom())
                offset = QPointF(0, 30 if down else -30)
            path = QPainterPath(start)
            if not workflow and not (
                ra.top() == rb.top() and abs(ra.center().x() - rb.center().x()) < 300
            ):
                # Route along grid gutters so an edge cannot appear to originate
                # from an unrelated box that happens to lie between its endpoints.
                start = QPointF(ra.center().x(), ra.bottom())
                end = QPointF(rb.left(), rb.center().y())
                path = QPainterPath(start)
                lane = ra.bottom() + 18
                gutter = rb.left() - 15
                path.lineTo(start.x(), lane)
                path.lineTo(gutter, lane)
                path.lineTo(gutter, end.y())
                path.lineTo(end)
                offset = QPointF(1, 0)
            else:
                path.cubicTo(start + offset, end - offset, end)
            line = self.scene.addPath(path, QPen(QColor(theme.PRIMARY), 2))
            line.setToolTip(f"{a} imports {b}" if not workflow else f"{a} → {b}")
            line.setZValue(-1)
            angle = atan2(offset.y(), offset.x())
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
        self.items[names[0]].setSelected(True)
        self.fit()

    def _selection(self) -> None:
        selected = self.scene.selectedItems()
        if not selected:
            return
        name = str(selected[0].data(0))
        workflow = self.mode.currentIndex() == 1
        sources = WORKFLOW if workflow else self.model.nodes
        details = "\n".join(sources.get(name, [])[:12])
        targets = [b for a, b in self.model.edges if a == name]
        self.details.setText(
            name
            + "\n"
            + details
            + ("\nImports: " + ", ".join(targets) if targets and not workflow else "")
        )
        for key, box in self.items.items():
            box.setBrush(QColor("#EFE4D9" if key == name else "#FFFFFF"))
            box.setPen(
                QPen(QColor("#632626" if key == name else "#A08C7E"), 3 if key == name else 1.5)
            )
        for a, _b, arrow in self.arrows:
            arrow.setVisible(workflow or a == name)
