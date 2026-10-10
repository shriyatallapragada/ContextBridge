"""Generic: ``HANDOFF.md`` at the repository root (Aider, OpenCode, anything else)."""

from __future__ import annotations

from pathlib import Path

from contextbridge.adapters.base_adapter import (
    BaseAdapter,
    has_block,
    read_text,
    render_block,
    write_text,
)


class GenericAdapter(BaseAdapter):
    name = "generic"

    @property
    def target_path(self) -> Path:
        return self.repo_root / "HANDOFF.md"

    def inject_payload(self, compressed_payload: str, active_files: list[str]) -> Path:
        self._guard_foreign_file()
        body = self._body(compressed_payload, active_files)
        write_text(self.target_path, render_block(body) + "\n")
        return self.target_path

    def cleanup(self) -> None:
        path = self.target_path
        if path.is_file() and has_block(read_text(path)):
            path.unlink()
