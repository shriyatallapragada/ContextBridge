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

The first-run screen defaults to offline mode and explains that no model or API key is needed. You can choose local Ollama or an OpenAI-compatible endpoint instead, and change this later in Settings. API keys are stored in the OS keyring. Connection checks run only when clicked. If a selected model is unavailable, the app uses its built-in offline summary.
