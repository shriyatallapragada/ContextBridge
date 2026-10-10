"""Run slow work (git, tests, AI) off the interface thread so the window never freezes.

Results come back through a QObject that lives on the interface thread, which makes Qt
queue every callback onto that thread. (A plain lambda connected to a signal can run on
the worker thread, and touching widgets from there crashes.)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

log = logging.getLogger("contextbridge")


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)
    progress = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[Callable[[str], None]], Any]) -> None:
        super().__init__()
        self.setAutoDelete(False)  # the runner owns it until its signals are delivered
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn(self.signals.progress.emit)
        except Exception as exc:
            log.exception("background task failed")
            self.signals.failed.emit(exc)
        else:
            self.signals.done.emit(result)


class _Delivery(QObject):
    """Lives on the interface thread; its slots run the caller's callbacks there."""

    def __init__(
        self,
        runner: TaskRunner,
        task: _Task,
        on_done: Callable[[Any], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[str], None] | None,
    ) -> None:
        super().__init__(runner)
        self._runner, self._task = runner, task
        self._on_done, self._on_error, self._on_progress = on_done, on_error, on_progress

    @Slot(object)
    def done(self, result: Any) -> None:
        self._runner._release(self._task)
        try:
            self._on_done(result)
        finally:
            self.deleteLater()

    @Slot(object)
    def failed(self, exc: Any) -> None:
        self._runner._release(self._task)
        try:
            self._on_error(exc)
        finally:
            self.deleteLater()

    @Slot(str)
    def progress(self, text: str) -> None:
        if self._on_progress is not None:
            self._on_progress(text)


class TaskRunner(QObject):
    """Starts background tasks and delivers their results on the interface thread."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active: set[_Task] = set()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)

    @property
    def busy(self) -> bool:
        return bool(self._active)

    def _release(self, task: _Task) -> None:
        self._active.discard(task)

    def run(
        self,
        fn: Callable[[Callable[[str], None]], Any],
        *,
        on_done: Callable[[Any], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        """Run ``fn(progress)`` in the background; call back on the interface thread.

        ``progress`` is a function the task can call with text (e.g. test output).
        """
        task = _Task(fn)
        self._active.add(task)
        delivery = _Delivery(self, task, on_done, on_error, on_progress)
        task.signals.done.connect(delivery.done)
        task.signals.failed.connect(delivery.failed)
        task.signals.progress.connect(delivery.progress)
        self._pool.start(task)

    def wait(self, msecs: int = 5000) -> bool:
        """Block until background work finishes (used when closing the app and in tests)."""
        return self._pool.waitForDone(msecs)
