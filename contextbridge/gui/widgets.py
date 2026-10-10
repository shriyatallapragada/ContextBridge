"""Small reusable pieces. Every screen is built from these so the look stays consistent."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from contextbridge.gui import assets, theme

CONTENT_MAX_WIDTH = 780


def label(text: str = "", kind: str = "", *, wrap: bool = True) -> QLabel:
    """A text label. ``kind``: H1, H2, Eyebrow, Muted, Small or Tag (see theme.py)."""
    lab = QLabel(text)
    if kind:
        lab.setObjectName(kind)
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    if kind == "Eyebrow":
        font = lab.font()
        font.setLetterSpacing(font.SpacingType.PercentageSpacing, 112)
        lab.setFont(font)
    return lab


def button(text: str, kind: str = "") -> QPushButton:
    """A push button. ``kind``: Primary, Danger, Link or empty for the quiet default."""
    btn = QPushButton(text)
    if kind:
        btn.setObjectName(kind)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # a focus ring for keyboard users only
    btn.setAccessibleName(text)
    return btn


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        child = item.layout()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif child is not None:
            clear_layout(child)


def add_shortcut(parent: QWidget, keys: str, handler: Callable[[], None]) -> QShortcut:
    """Bind a keyboard shortcut (Ctrl becomes Cmd on a Mac) to ``handler``."""
    shortcut = QShortcut(QKeySequence(keys), parent)
    shortcut.activated.connect(handler)
    return shortcut


class Card(QFrame):
    """A softly bordered panel. Put content in ``card.body``."""

    def __init__(self, parent: QWidget | None = None, *, selected: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("CardSelected" if selected else "Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 18, 20, 18)
        self.body.setSpacing(8)


class Notice(QFrame):
    """A calm inline message (information, a gentle warning, a result)."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Notice")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        self._text = label(text)
        lay.addWidget(self._text)

    def set_text(self, text: str) -> None:
        self._text.setText(text)


class EmptyState(QWidget):
    """A friendly picture, a sentence, and (optionally) the next step."""

    def __init__(
        self,
        picture: str,
        title: str,
        body: str,
        action: QPushButton | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.setSpacing(10)
        lay.setContentsMargins(0, 24, 0, 24)
        pic = QLabel()
        pic.setPixmap(assets.illustration(picture, 150))
        pic.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        pic.setAccessibleName("")  # decorative
        title_label = label(title, "H2")
        title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        body_label = label(body, "Muted")
        body_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        body_label.setMaximumWidth(440)
        lay.addWidget(pic)
        lay.addWidget(title_label)
        lay.addWidget(body_label, 0, Qt.AlignmentFlag.AlignHCenter)
        if action is not None:
            lay.addSpacing(6)
            lay.addWidget(action, 0, Qt.AlignmentFlag.AlignHCenter)


class ChipRow(QWidget):
    """A row of mutually exclusive pill buttons. ``changed`` carries the chosen key."""

    changed = Signal(str)

    def __init__(self, choices: list[tuple[str, str, str]], parent: QWidget | None = None) -> None:
        """``choices`` is (key, text, tooltip)."""
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, text, tip in choices:
            btn = QPushButton(text)
            btn.setObjectName("Chip")
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn.setAccessibleName(f"{text}: {tip}")
            self._group.addButton(btn)
            self._buttons[key] = btn
            btn.clicked.connect(lambda _checked=False, k=key: self.changed.emit(k))
            lay.addWidget(btn)
        lay.addStretch(1)

    def set_value(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)

    def value(self) -> str | None:
        for key, btn in self._buttons.items():
            if btn.isChecked():
                return key
        return None


class ChoiceCard(QPushButton):
    """A big checkable card (used to pick who you are handing off to)."""

    def __init__(
        self, title: str, blurb: str, footnote: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ChoiceCard")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setMinimumHeight(138)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(f"{title}. {blurb} Writes {footnote}.")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(4)
        for lab in (label(title, "H2"), label(blurb, "Muted"), label(footnote, "Small")):
            lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(lab)
        lay.addStretch(1)


class Spinner(QProgressBar):
    """A slim 'working on it' bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, 0)
        self.setTextVisible(False)
        self.setFixedHeight(8)
        self.hide()


class Toast(QLabel):
    """A short message that fades in at the bottom of the window, then goes away."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setStyleSheet(
            f"QLabel#Toast {{ background: {theme.RED_CHAI}; color: {theme.ON_DARK}; "
            f"border-radius: 10px; padding: 10px 20px; }}"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self.hide()

    def show_message(self, text: str, msecs: int = 2600) -> None:
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._anim.stop()
        self._anim.setDuration(180)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(msecs)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setDuration(300)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._after_fade)
        self._anim.start()

    def _after_fade(self) -> None:
        self._anim.finished.disconnect(self._after_fade)
        self.hide()


class Page(QWidget):
    """A scrolling page with one centred column. Add content to ``self.body``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("Page")
        row = QHBoxLayout(holder)
        row.setContentsMargins(40, 34, 40, 44)
        column = QWidget()
        column.setObjectName("Page")
        column.setMaximumWidth(CONTENT_MAX_WIDTH)
        self.body = QVBoxLayout(column)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(18)
        row.addStretch(1)
        row.addWidget(column, 100, Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll)

    def heading(self, eyebrow: str, title: str, subtitle: str = "") -> None:
        self.body.addWidget(label(eyebrow.upper(), "Eyebrow"))
        self.body.addWidget(label(title, "H1"))
        if subtitle:
            self.body.addWidget(label(subtitle, "Muted"))
        self.body.addSpacing(6)


def pixmap_label(pixmap: QPixmap) -> QLabel:
    lab = QLabel()
    lab.setPixmap(pixmap)
    return lab
