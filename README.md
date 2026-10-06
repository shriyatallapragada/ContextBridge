# ContextBridge

Local-first CLI (`cb`) that preserves project context across AI coding agents.

> Status: Phase 2 (git + AST ingestion, test runs). See `contextbridge_spec.md`.

```bash
uv sync --all-extras
uv run cb init                      # also installs a git post-commit hook
uv run cb log "Switched auth to JWT" --tag architecture
uv run cb run -- pytest -q          # runs tests, records pass/fail counts
uv run cb status
```

## Git hook

`cb init` adds a block to `.git/hooks/post-commit` (or your `core.hooksPath`). The block
sits between `# >>> contextbridge >>>` and `# <<< contextbridge <<<`. An existing hook is
kept, and the block is appended to it. The hook runs `cb hook post-commit` in the
background and always exits 0. On each commit it records the changed files and the
functions, classes and methods each change touched (Python, JS/TS, Go, Rust). Binary,
ignored (`capture.ignore_globs`) and secret-looking files (`.env*`, `*.pem`, ...) are skipped.

- Skip it: `cb init --no-hooks`
- Remove it: `cb init --uninstall-hooks` (removes only the ContextBridge block)

## Recording test runs

`cb run -- <cmd>` streams the command's output unchanged and exits with its exit code.
It parses results from pytest, jest, vitest, `go test` and `cargo test`. For other
commands it stores the exit code and the last 20 lines of output (sanitized).

Optional shell alias:

```bash
alias t='cb run -- pytest'
```
