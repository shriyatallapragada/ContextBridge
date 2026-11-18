"""Safe provider inspection and an explicit tiny generation probe."""

from __future__ import annotations

import typer
from rich.markup import escape

from contextbridge.cli.common import app_context, console, error_boundary
from contextbridge.config import load_config
from contextbridge.errors import NotInitializedError
from contextbridge.providers import DEFAULT_ENDPOINTS
from contextbridge.providers.settings import ProviderSettingsService
from contextbridge.storage.workspace import find_context_dir

provider_app = typer.Typer(
    help="Inspect or test the selected provider; never display credentials.", no_args_is_help=True
)


def _settings(ctx: typer.Context) -> ProviderSettingsService:
    try:
        directory = find_context_dir(app_context(ctx).start_dir)
    except NotInitializedError:
        directory = None
    return ProviderSettingsService(directory)


def _selection(settings: ProviderSettingsService) -> tuple[str, str, str]:
    cfg = load_config(settings.context_dir).llm
    model = cfg.cloud_model_for(cfg.provider) if cfg.provider in DEFAULT_ENDPOINTS else cfg.model
    endpoint = (
        cfg.ollama_host
        if cfg.provider == "ollama"
        else DEFAULT_ENDPOINTS.get(cfg.provider, cfg.base_url)
    )
    return cfg.provider, model, endpoint


@provider_app.command("show")
def provider_show(ctx: typer.Context) -> None:
    """Show effective provider, model, endpoint and configuration paths, without reading the key."""
    with error_boundary():
        settings = _settings(ctx)
        provider, model, endpoint = _selection(settings)
        console.print(escape(f"Provider: {provider}"))
        if provider != "none":
            # Reject credential-bearing URLs before displaying any configuration.
            from contextbridge.providers.http import validate_endpoint

            endpoint = validate_endpoint(endpoint, local=provider == "ollama")
            from contextbridge.core.sanitizer import sanitize

            console.print(escape(sanitize(f"Model: {model}\nEndpoint: {endpoint}")))
        console.print(escape(f"Configuration: {settings.path}"))
        console.print(
            "API key: never displayed; credentials are read only when testing or generating."
        )


@provider_app.command("test")
def provider_test(ctx: typer.Context) -> None:
    """Send a tiny test prompt to the effective model; no project context is included."""
    with error_boundary():
        settings = _settings(ctx)
        provider, model, endpoint = _selection(settings)
        if provider != "none":
            console.print("Sending a tiny model test prompt; no project context is included.")
        result = settings.check(provider, model, endpoint)
        console.print(escape(result.message))
        if not result.ok:
            raise typer.Exit(1)
