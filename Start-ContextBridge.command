#!/bin/sh
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
trap 'printf "\nCould not start ContextBridge. Check the message above.\nPress Return to close."; read -r answer' EXIT
command -v uv >/dev/null 2>&1 || { echo 'Install uv first: https://docs.astral.sh/uv/'; exit 1; }
command -v npm >/dev/null 2>&1 || { echo 'Install Node.js first: https://nodejs.org/'; exit 1; }
if [ ! -d frontend/node_modules ]; then npm --prefix frontend ci; fi
npm --prefix frontend run build
trap - EXIT
exec uv run --extra api cb serve --open
