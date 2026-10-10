#!/usr/bin/env python
"""Build the standalone ContextBridge app for this computer.

    uv sync --all-extras
    uv run python scripts/build_app.py

Steps: draw the icons, save the tokenizer data inside the app, run PyInstaller, then check
that the finished app really starts and can do everything it needs (``--self-test``).
Use ``--skip-icons`` to reuse icons you have already made.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "packaging" / "icons"
BUILD = ROOT / "build"
DIST = ROOT / "dist"


def make_icons() -> None:
    """Render the app icon (a designer's app_icon.svg if present) into .icns / .ico files."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    from PySide6.QtWidgets import QApplication

    from contextbridge.gui import assets

    _app = QApplication.instance() or QApplication([])
    ICONS.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        iconset = ICONS / "ContextBridge.iconset"
        shutil.rmtree(iconset, ignore_errors=True)
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            assets.app_icon_sized(base).save(str(iconset / f"icon_{base}x{base}.png"), "PNG")
            assets.app_icon_sized(base * 2).save(str(iconset / f"icon_{base}x{base}@2x.png"), "PNG")
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(ICONS / "ContextBridge.icns")],
            check=True,
        )
        shutil.rmtree(iconset)
    else:
        assets.app_icon_sized(256).save(str(ICONS / "ContextBridge.ico"), "ICO")
        assets.app_icon_sized(256).save(str(ICONS / "ContextBridge.png"), "PNG")
    print(f"icons written to {ICONS}")


def save_tokenizer_data() -> None:
    """Download the tokenizer file once, into build/, so the app can carry it."""
    cache = BUILD / "tiktoken_cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
    import tiktoken

    tiktoken.get_encoding("cl100k_base")
    size_kb = sum(f.stat().st_size for f in cache.iterdir()) // 1024
    print(f"tokenizer data saved to {cache} ({size_kb} KB)")


def run_pyinstaller() -> None:
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "ContextBridge.spec"),
            "--noconfirm", "--clean",
            "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
        ],
        check=True,
        cwd=ROOT,
    )  # fmt: skip


def built_executable() -> Path:
    if sys.platform == "darwin":
        return DIST / "ContextBridge.app" / "Contents" / "MacOS" / "ContextBridge"
    name = "ContextBridge.exe" if os.name == "nt" else "ContextBridge"
    return DIST / "ContextBridge" / name


def check_built_app() -> None:
    exe = built_executable()
    if not exe.exists():
        raise SystemExit(f"build finished but {exe} is missing")
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    for args in (["--version"], ["--cli", "--version"], ["--self-test"]):
        proc = subprocess.run(
            [str(exe), *args], capture_output=True, text=True, env=env, timeout=180
        )
        shown = (proc.stdout + proc.stderr).strip().splitlines()
        print(
            f"  {exe.name} {' '.join(args)} -> exit {proc.returncode}: {shown[-1] if shown else ''}"
        )
        if proc.returncode != 0:
            print(proc.stdout, proc.stderr)
            raise SystemExit(f"the built app failed: {' '.join(args)}")


def folder_size_mb(path: Path) -> float:
    return (
        sum(f.lstat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--skip-icons", action="store_true", help="reuse icons already in packaging/icons"
    )
    args = parser.parse_args()
    print(f"Building ContextBridge on {platform.system()} {platform.machine()}")
    if not args.skip_icons:
        make_icons()
    save_tokenizer_data()
    run_pyinstaller()
    check_built_app()
    target = DIST / ("ContextBridge.app" if sys.platform == "darwin" else "ContextBridge")
    print(f"\nDone: {target}  ({folder_size_mb(target):.0f} MB)")


if __name__ == "__main__":
    main()
