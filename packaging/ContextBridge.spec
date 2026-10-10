# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the standalone ContextBridge app.

Build with:  python scripts/build_app.py
Produces:    dist/ContextBridge.app (macOS) or dist/ContextBridge/ (Windows, Linux)
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821  (SPECPATH is provided)
sys.path.insert(0, ROOT)
from contextbridge import __version__  # noqa: E402

datas = [(os.path.join(ROOT, "contextbridge", "gui", "assets"), "contextbridge/gui/assets")]
binaries = []
hiddenimports = collect_submodules("contextbridge")

# Packages with native libraries or plugin discovery that PyInstaller cannot see on its own.
for package in (
    "tree_sitter",
    "tree_sitter_language_pack",
    "tree_sitter_c_sharp",
    "tree_sitter_embedded_template",
    "tree_sitter_yaml",
    "tiktoken",
    "tiktoken_ext",
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden
hiddenimports += ["tiktoken_ext.openai_public"]

# The language pack ships compiled grammars for ~170 languages (about 180 MB). ContextBridge
# reads exactly these six, so only they go into the app.
KEEP_GRAMMARS = {"python", "javascript", "typescript", "tsx", "go", "rust"}
GRAMMAR_MARKER = "tree_sitter_language_pack/bindings/"


def _grammar_name(path):
    """The language a bundled grammar file belongs to, or None if it isn't a grammar."""
    text = str(path).replace("\\", "/")
    if GRAMMAR_MARKER not in text:
        return None
    return text.split(GRAMMAR_MARKER, 1)[1].split("/")[0].split(".")[0]


def _wanted(entry):
    """Keep everything except grammars of languages we don't use. ``entry`` is (src, dest)."""
    name = _grammar_name(entry[0]) or _grammar_name(entry[1])
    return name is None or name in KEEP_GRAMMARS or name == "__init__"


binaries = [b for b in binaries if _wanted(b)]
datas = [d for d in datas if _wanted(d)]
hiddenimports = [
    h
    for h in hiddenimports
    if not h.startswith("tree_sitter_language_pack.bindings.")
    or h.rsplit(".", 1)[-1] in KEEP_GRAMMARS
]

# The tokenizer data, so counting works on the very first run, even offline.
cache = os.path.join(ROOT, "build", "tiktoken_cache")
if os.path.isdir(cache):
    datas.append((cache, "tiktoken_cache"))

# Left out on purpose: heavy optional features the app doesn't use (the command line keeps them).
excludes = [
    "chromadb", "onnxruntime", "numpy", "pandas", "scipy", "matplotlib",
    "openai", "groq", "tkinter", "_tkinter", "pytest", "IPython",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
]  # fmt: skip

a = Analysis(
    [os.path.join(ROOT, "packaging", "entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = None
for candidate in ("ContextBridge.icns", "ContextBridge.ico"):
    path = os.path.join(ROOT, "packaging", "icons", candidate)
    if os.path.exists(path) and candidate.endswith(".icns" if sys.platform == "darwin" else ".ico"):
        icon = path

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ContextBridge",
    console=False,  # a windowed app: no terminal window
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ContextBridge")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ContextBridge.app",
        icon=icon,
        bundle_identifier="app.contextbridge.ContextBridge",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": "ContextBridge",
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "ContextBridge",
        },
    )
