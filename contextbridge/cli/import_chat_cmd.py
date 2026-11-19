"""Condense quoted conversation text into the shared handoff format."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from contextbridge import clipboard
from contextbridge.chat_bridge import MAX_CHAT_CHARS, chat_warnings, condense_chat
from contextbridge.cli.common import app_context, err_console, error_boundary
from contextbridge.config import load_config
from contextbridge.errors import NotInitializedError, UserError
from contextbridge.storage.workspace import Workspace


def import_chat_command(
    ctx: typer.Context,
    file: Path | None = typer.Option(None, "--file", help="UTF-8 text or Markdown file."),
    from_clipboard: bool = typer.Option(False, "--clipboard", help="Read clipboard text."),
    copy: bool = typer.Option(False, "--copy", help="Copy the result to the clipboard."),
    goal: str = typer.Option("", "--goal", help="Goal for the next chatbot."),
    budget: int | None = typer.Option(None, "--budget", min=120, help="Maximum output tokens."),
) -> None:
    """Read stdin, --file, or --clipboard and print a redacted, structured handoff."""
    with error_boundary():
        if file and from_clipboard:
            raise UserError("Choose either --file or --clipboard.")
        try:
            if file:
                with file.open(encoding="utf-8-sig") as stream:
                    text = stream.read(MAX_CHAT_CHARS + 1)
            elif from_clipboard:
                text = clipboard.read_text()
            else:
                if sys.stdin.isatty():
                    err_console.print(
                        "Paste the chat, then press Ctrl-D (Ctrl-Z on Windows) to finish."
                    )
                text = sys.stdin.read(MAX_CHAT_CHARS + 1)
            try:
                config = Workspace.locate(app_context(ctx).start_dir).load_config()
            except NotInitializedError:
                config = load_config()
            cap = budget or min(config.handoff.target_tokens, config.handoff.hard_cap_tokens)
            output = condense_chat(text, goal=goal, budget=cap)
            for warning in chat_warnings(text):
                err_console.print(warning)
        except (OSError, UnicodeError, ValueError) as exc:
            raise UserError(str(exc)) from exc
        if copy:
            clipboard.copy_text(output)
            err_console.print("Copied chat handoff to clipboard.")
        typer.echo(output, nl=False)
