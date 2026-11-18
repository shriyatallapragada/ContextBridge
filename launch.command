#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
APP="$ROOT/dist/ContextBridge/ContextBridge"
if command -v uv >/dev/null 2>&1 && [ -f "$ROOT/pyproject.toml" ]; then
  cd "$ROOT"
  exec uv run --extra gui contextbridge-gui
fi
if [ -x "$APP" ]; then
  exec "$APP"
fi
printf '%s\n' 'ContextBridge cannot find uv or a built app.'
printf '%s\n' 'Install uv to run this checkout, or build a standalone app; see LAUNCH.md.'
exit 1
