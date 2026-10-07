"""Claude Code: a ``<context_handoff>`` block inside ``CLAUDE.md``."""

from __future__ import annotations

from pathlib import Path

from contextbridge.adapters.base_adapter import (
    BaseAdapter,
    check_balanced,
    edit_text,
    has_block,
    read_text,
    remove_blocks,
    render_block,
    upsert_block,
)


class ClaudeAdapter(BaseAdapter):
    name = "claude"

    @property
    def target_path(self) -> Path:
        return self.repo_root / "CLAUDE.md"

    def inject_payload(self, compressed_payload: str, active_files: list[str]) -> Path:
        path = self.target_path
        body = self._body(compressed_payload, active_files)
        block = render_block(f"<context_handoff>\n{body}\n</context_handoff>")
        if path.exists():
            check_balanced(read_text(path), path)
        edit_text(path, lambda text: upsert_block(text, block))
        return path

    def cleanup(self) -> None:
        path = self.target_path
        if not path.is_file():
            return
        text = read_text(path)
        check_balanced(text, path)
        if not has_block(text):
            return
        edit_text(path, remove_blocks)
        if not read_text(path).strip():  # we created it and nothing else is left
            path.unlink()
