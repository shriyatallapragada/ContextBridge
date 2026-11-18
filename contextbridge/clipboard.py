"""Clipboard access without importing a desktop UI."""

from __future__ import annotations

import shutil
import subprocess
import sys

from contextbridge.errors import UserError


def _command(read: bool) -> list[str]:
    if sys.platform == "darwin":
        return ["pbpaste" if read else "pbcopy"]
    if sys.platform == "win32":
        script = (
            "Get-Clipboard -Raw" if read else "Set-Clipboard -Value ([Console]::In.ReadToEnd())"
        )
        return ["powershell", "-NoProfile", "-Command", script]
    choices = (
        [["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"]]
        if read
        else [["wl-copy"], ["xclip", "-selection", "clipboard"]]
    )
    for command in choices:
        if shutil.which(command[0]):
            return command
    raise UserError(
        "Clipboard access is unavailable. Use stdin, --file, or --print on this computer."
    )


def copy_text(text: str) -> None:
    _run(False, text)


def read_text() -> str:
    return _run(True)


def _run(read: bool, text: str | None = None) -> str:
    try:
        result = subprocess.run(
            _command(read),
            input=text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UserError(
            "The clipboard could not be accessed. Use a text file or terminal output."
        ) from exc
    return result.stdout
