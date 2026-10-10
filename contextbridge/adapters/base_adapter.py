"""Adapter base class and the shared marker-block helpers.

A handoff lives in a block between :data:`MARKER_START` and :data:`MARKER_END`.
Re-injecting replaces the block; cleanup removes only the block, leaving everything
else in the file byte for byte as the user wrote it.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from contextbridge.core.sanitizer import sanitize
from contextbridge.errors import AdapterConflictError, AdapterError

MARKER_START = "<!-- contextbridge:start -->"
MARKER_END = "<!-- contextbridge:end -->"
CLOSING_TAG = "</context_handoff>"
ACTIVE_FILES_HEADING = "## Active files"
MAX_ACTIVE_FILES = 10

_BLOCK = re.compile(re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END), re.S)


# ----- block helpers (pure text) ------------------------------------------------------


def has_block(text: str) -> bool:
    return MARKER_START in text


def render_block(body: str) -> str:
    return f"{MARKER_START}\n{body.strip()}\n{MARKER_END}"


def check_balanced(text: str, path: Path | None = None) -> None:
    """Raise AdapterError unless every start marker is paired with a later end marker."""
    if text.count(MARKER_START) != len(_BLOCK.findall(text)) or text.count(MARKER_END) != len(
        _BLOCK.findall(text)
    ):
        where = f" in {path.name}" if path else ""
        raise AdapterError(
            f"Unbalanced ContextBridge markers{where}; refusing to edit the file.",
            hint=f"Fix or delete the stray {MARKER_START} / {MARKER_END} lines by hand.",
        )


def remove_blocks(text: str) -> str:
    """Remove every block, plus the newline after it and the blank line we added before it."""
    out: list[str] = []
    pos = 0
    for m in _BLOCK.finditer(text):
        start, end = m.start(), m.end()
        if text.startswith("\n", end):
            end += 1
        # The separator blank line we add when appending at the end of a file.
        if end == len(text) and start >= 2 and text[start - 1] == "\n" and text[start - 2] == "\n":
            start -= 1
        out.append(text[pos:start])
        pos = end
    out.append(text[pos:])
    return "".join(out)


def upsert_block(text: str, block: str) -> str:
    """Replace the existing block in place, or append one (separated by a blank line)."""
    first = _BLOCK.search(text)
    if first is None:
        if not text:
            return block + "\n"
        return text + ("" if text.endswith("\n") else "\n") + "\n" + block + "\n"
    return text[: first.start()] + block + remove_blocks(text[first.end() :])


def neutralize(body: str) -> str:
    """Stop payload text from forging markers or closing the wrapper tag early."""
    body = body.replace(MARKER_START, "<!-- contextbridge: start -->")
    body = body.replace(MARKER_END, "<!-- contextbridge: end -->")
    return body.replace(CLOSING_TAG, CLOSING_TAG.replace("</", "<\\/"))


# ----- file helpers --------------------------------------------------------------------


def read_text(path: Path) -> str:
    """Read ``path`` byte-preserving (undecodable bytes survive a round trip)."""
    return path.read_bytes().decode("utf-8", errors="surrogateescape")


def write_text(path: Path, text: str) -> None:
    """Atomically write ``text`` (same encoding rules as read_text), keeping file mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8", errors="surrogateescape"))
        if path.exists():
            shutil.copymode(path, tmp)
        else:
            tmp.chmod(0o644)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def edit_text(path: Path, transform: Callable[[str], str]) -> None:
    """Read ``path`` ('' if missing), apply ``transform`` on LF text, write it back.

    Files that use CRLF throughout keep CRLF.
    """
    text = read_text(path) if path.exists() else ""
    crlf = "\r\n" in text and "\n" not in text.replace("\r\n", "")
    if crlf:
        text = text.replace("\r\n", "\n")
    new = transform(text)
    write_text(path, new.replace("\n", "\r\n") if crlf else new)


# ----- the interface -------------------------------------------------------------------


class BaseAdapter(ABC):
    """Writes a handoff payload into one agent's instruction file."""

    name: str

    def __init__(self, repo_root: Path, *, force: bool = False) -> None:
        self.repo_root = repo_root
        self.force = force

    @property
    @abstractmethod
    def target_path(self) -> Path:
        """The file this adapter writes."""

    @abstractmethod
    def inject_payload(self, compressed_payload: str, active_files: list[str]) -> Path:
        """Write the payload to the target agent's file and return the path written."""

    @abstractmethod
    def cleanup(self) -> None:
        """Remove only the content ContextBridge injected."""

    # ----- shared behaviour --------------------------------------------------------

    @property
    def relative_path(self) -> str:
        return self.target_path.relative_to(self.repo_root).as_posix()

    def is_installed(self) -> bool:
        """True if the target file carries a ContextBridge block."""
        path = self.target_path
        return path.is_file() and has_block(read_text(path))

    def _body(self, payload: str, active_files: list[str]) -> str:
        """The sanitized, marker-safe text to write.

        Payloads from :func:`build_handoff` already list the active files; the list is
        appended only when it is missing, so it is never duplicated.
        """
        body = sanitize(payload).strip()
        files = [sanitize(f) for f in active_files[:MAX_ACTIVE_FILES]]
        if files and ACTIVE_FILES_HEADING not in body:
            body += f"\n\n{ACTIVE_FILES_HEADING}\n" + "\n".join(f"- {f}" for f in files)
        return neutralize(body)

    def _guard_foreign_file(self) -> None:
        """Refuse to overwrite a file ContextBridge did not create (unless forced)."""
        path = self.target_path
        if path.exists() and not has_block(read_text(path)) and not self.force:
            raise AdapterConflictError(
                f"{self.relative_path} already exists and was not created by ContextBridge.",
                hint="Re-run with --force to overwrite it, or move it aside first.",
            )
