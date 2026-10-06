# ContextBridge

Local-first CLI (`cb`) that preserves project context across AI coding agents.

> Status: Phase 1 (CLI shell, database, sanitizer). See `contextbridge_spec.md`.

```bash
uv sync --all-extras
uv run cb init
uv run cb log "Switched auth to JWT" --tag architecture
uv run cb status
```
