"""Helpers for driving the desktop app in tests (offscreen, no real screen)."""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from contextbridge.gui.mainwindow import MainWindow


def pump(app: QApplication, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.003)


def settle(app: QApplication, window: MainWindow, timeout: float = 20.0) -> None:
    """Process events until all background work (and the follow-up work it starts) is done."""
    deadline = time.time() + timeout
    quiet = 0
    while time.time() < deadline:
        app.processEvents()
        if window.runner.busy:
            quiet = 0
            time.sleep(0.004)
            continue
        quiet += 1
        if quiet >= 8:  # idle for several rounds: nothing is about to start another task
            return
        time.sleep(0.003)
    raise AssertionError("background work did not finish in time")


def wait_for(app: QApplication, predicate: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not met in time")


def buttons(widget: QWidget) -> list[QPushButton]:
    return [b for b in widget.findChildren(QPushButton) if b.isVisibleTo(widget)]


def find_button(widget: QWidget, text: str) -> QPushButton:
    """The visible button whose label is ``text`` (ignoring surrounding spaces)."""
    for b in buttons(widget):
        if b.text().strip() == text:
            return b
    labels = [b.text().strip() for b in buttons(widget)]
    raise AssertionError(f"no visible button {text!r}; found {labels}")


def has_button(widget: QWidget, text: str) -> bool:
    return any(b.text().strip() == text for b in buttons(widget))
