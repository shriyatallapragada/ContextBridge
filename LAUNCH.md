# Start ContextBridge

## From a project checkout on macOS

Double-click `launch.command` in Finder, or run `./launch.command` from Terminal. When `uv` is installed, it launches the current source with the GUI dependency, so new features are available without rebuilding. If `uv` is not available, it opens the bundled standalone app in `dist/ContextBridge` when present.

If macOS blocks the first launch, Control-click `launch.command`, choose **Open**, then confirm **Open**. This is only needed on first launch for this unsigned local script.

## Standalone app

A distributed standalone app does not need Python or `uv`. To build it on the target operating system:

```sh
uv sync --extra gui --extra build
uv run python scripts/build_app.py
```

## AI summaries

Ollama is optional. With the default settings, ContextBridge tries Ollama and falls back to its built-in offline summary if Ollama or the configured model is unavailable. No API key or Ollama installation is needed to launch or use the app. Install and run Ollama only if you want AI generated summaries.
