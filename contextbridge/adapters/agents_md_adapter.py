"""Shared implementation for agents that read the repository's ``AGENTS.md``."""

from __future__ import annotations

from pathlib import Path

from contextbridge.adapters.base_adapter import (
    BaseAdapter,
    edit_text,
    has_block,
    read_text,
    remove_blocks,
    render_block,
    upsert_block,
)


class AgentsMdAdapter(BaseAdapter):
    """Preserve user instructions and upsert only ContextBridge's marked handoff."""

    @property
    def target_path(self) -> Path:
        return self.repo_root / "AGENTS.md"

    def inject_payload(self, compressed_payload: str, active_files: list[str]) -> Path:
        block = render_block(self._body(compressed_payload, active_files))
        edit_text(self.target_path, lambda text: upsert_block(text, block))
        return self.target_path

    def cleanup(self) -> None:
        path = self.target_path
        if not path.is_file() or not has_block(read_text(path)):
            return
        edit_text(path, remove_blocks)
        if not read_text(path).strip():
            path.unlink()
