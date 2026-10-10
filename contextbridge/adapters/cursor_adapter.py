"""Cursor: an always-applied rule file at ``.cursor/rules/handoff.mdc``."""

from __future__ import annotations

from pathlib import Path

from contextbridge.adapters.base_adapter import (
    BaseAdapter,
    has_block,
    read_text,
    render_block,
    write_text,
)

# Frontmatter must be the first thing in an .mdc file, so it sits outside the markers.
FRONTMATTER = (
    "---\ndescription: ContextBridge session handoff (auto-generated)\nalwaysApply: true\n---\n"
)


class CursorAdapter(BaseAdapter):
    name = "cursor"

    @property
    def target_path(self) -> Path:
        return self.repo_root / ".cursor" / "rules" / "handoff.mdc"

    def inject_payload(self, compressed_payload: str, active_files: list[str]) -> Path:
        self._guard_foreign_file()
        body = self._body(compressed_payload, active_files)
        write_text(self.target_path, f"{FRONTMATTER}\n{render_block(body)}\n")
        return self.target_path

    def cleanup(self) -> None:
        path = self.target_path
        if path.is_file() and has_block(read_text(path)):
            path.unlink()
            # Remove the directories we created, but only if nothing else lives there.
            for parent in (path.parent, path.parent.parent):
                try:
                    parent.rmdir()
                except OSError:
                    break
