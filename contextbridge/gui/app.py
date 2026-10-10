"""Entry point for the desktop app (and for the packaged app's `--cli` mode)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

COMMON_PATHS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")


def _extend_path() -> None:
    """Apps started from Finder get a bare PATH; add the usual places for git and friends."""
    if os.name == "nt":
        return
    current = os.environ.get("PATH", "").split(os.pathsep)
    extra = [p for p in COMMON_PATHS if p not in current and Path(p).is_dir()]
    os.environ["PATH"] = (
        os.pathsep.join([*current, *extra]) if current != [""] else os.pathsep.join(extra)
    )


def _use_bundled_data() -> None:
    """In a packaged app, point tiktoken at the encoding shipped inside it (works offline)."""
    base = getattr(sys, "_MEIPASS", None)
    if base and "TIKTOKEN_CACHE_DIR" not in os.environ:
        bundled = Path(base) / "tiktoken_cache"
        if bundled.is_dir():
            os.environ["TIKTOKEN_CACHE_DIR"] = str(bundled)


def run_cli(args: list[str]) -> int:
    """Behave exactly like the `cb` command (used by git hooks inside a packaged app)."""
    _use_bundled_data()
    if sys.stdout is None:  # windowed apps can start without console streams
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")  # noqa: SIM115
    from contextbridge.main import app

    app(args=args, prog_name="cb")
    return 0


def self_test() -> int:
    """Check that everything a packaged app needs is present, without showing a window."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    problems: list[str] = []

    def check(name: str, fn) -> None:  # type: ignore[no-untyped-def]
        try:
            fn()
        except Exception as exc:
            problems.append(f"{name}: {type(exc).__name__}: {exc}")

    def parse() -> None:
        from contextbridge.core.ast_parser import extract_symbols

        samples = (
            ("python", "def f():\n    pass\n"),
            ("javascript", "function f() {}\n"),
            ("typescript", "function f(): void {}\n"),
            ("tsx", "function F() { return <div/>; }\n"),
            ("go", "package p\nfunc F() {}\n"),
            ("rust", "fn f() {}\n"),
        )
        for lang, src in samples:
            if not extract_symbols(src, lang):
                raise RuntimeError(f"no symbols found for {lang}")

    def tokens() -> None:
        from contextbridge.synthesis import token_counter

        if token_counter.is_estimate():
            raise RuntimeError("tiktoken encoding unavailable (counts would be estimates)")

    def git() -> None:
        from contextbridge.service import git_is_installed

        if not git_is_installed():
            raise RuntimeError("git not found")

    def window() -> None:
        from PySide6.QtWidgets import QApplication

        from contextbridge.gui.mainwindow import MainWindow

        qt = QApplication.instance() or QApplication([])
        win = MainWindow(restore_last=False)
        win.show()
        qt.processEvents()
        win.close()

    check("code parsing", parse)
    check("token counting", tokens)
    check("git", git)
    check("window", window)
    for line in problems:
        print(f"PROBLEM  {line}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


def configure_app(qt: QApplication) -> None:
    """Apply the ContextBridge look to a QApplication."""
    from PySide6.QtGui import QFont

    from contextbridge.gui import assets, theme

    qt.setApplicationName("ContextBridge")
    qt.setOrganizationName("ContextBridge")
    qt.setWindowIcon(assets.app_icon())
    qt.setStyle("Fusion")  # a neutral base the stylesheet fully controls
    font = QFont(qt.font())
    font.setPointSize(theme.BASE_FONT_PT)
    qt.setFont(font)
    qt.setStyleSheet(theme.build_stylesheet(assets.indicator_images()))


def run_gui(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from contextbridge.gui.mainwindow import MainWindow

    _extend_path()
    _use_bundled_data()
    qt = QApplication(["ContextBridge", *(argv or [])])
    configure_app(qt)
    window = MainWindow()
    geometry = window.settings.value("geometry")
    if geometry:
        window.restoreGeometry(geometry)
    window.show()
    return qt.exec()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--cli"]:
        return run_cli(args[1:])
    if args[:1] == ["--version"]:
        from contextbridge import __version__

        print(f"ContextBridge {__version__}")
        return 0
    if args[:1] == ["--self-test"]:
        return self_test()
    return run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
