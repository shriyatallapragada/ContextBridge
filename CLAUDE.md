# ContextBridge

Local-first CLI (`cb`) that preserves project context across AI coding agents.
Full spec: `contextbridge_spec.md`. Read only the sections relevant to the current phase.

## Rules
- Work ONE phase at a time. Stop when its acceptance criteria pass and show test output. Do not start the next phase.
- Python 3.11+, fully type-hinted. Typer + Rich CLI, stdlib sqlite3.
- Sanitize all text before storing or sending it anywhere.
- Prefer small, focused changes. Run tests after changes and fix failures yourself.

## Commands
- Install deps: `uv sync --all-extras`
- Test: `uv run pytest -q`
- Lint: `uv run ruff check . && uv run mypy contextbridge`
- Run CLI: `uv run cb --help`
