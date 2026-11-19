"""Explicit provider setup for terminal users."""

from pathlib import Path

import typer

from contextbridge.cli.common import console, error_boundary, open_workspace
from contextbridge.providers.settings import ProviderSettingsService


def configure_command(
    ctx: typer.Context,
    provider: str = typer.Option("none", "--provider", help="none, ollama, or openai-compatible"),
    model: str = typer.Option("", "--model", help="The model name from your provider."),
    endpoint: str = typer.Option(
        "", "--endpoint", help="Ollama host or OpenAI-compatible base URL."
    ),
    set_key: bool = typer.Option(False, "--set-key", help="Prompt securely for an API key."),
    global_settings: bool = typer.Option(False, "--global", help="Save defaults for all projects."),
) -> None:
    """Choose offline, Ollama, or a hosted API. API keys are entered in a hidden prompt."""
    with error_boundary():
        context_dir: Path | None = None if global_settings else open_workspace(ctx).context_dir
        key = typer.prompt("API key", hide_input=True) if set_key else ""
        service = ProviderSettingsService(context_dir)
        service.save(provider, model, endpoint, key)
        console.print("Model settings saved. Offline features are ready.")
