# ContextBridge

Local-first CLI (`cb`) that preserves project context across AI coding agents.

> Status: Phase 3 (synthesis: context building, compression, status, search). See `contextbridge_spec.md`.

```bash
uv sync --all-extras
uv run cb init                      # also installs a git post-commit hook
uv run cb log "Switched auth to JWT" --tag architecture
uv run cb run -- pytest -q          # runs tests, records pass/fail counts
uv run cb status                    # dashboard: changes, symbols, tests, token stats
uv run cb handoff --dry-run --from claude --to cursor   # preview the handoff payload
uv run cb search "cents"            # search decisions and changes
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

## Handoff preview

`cb handoff --dry-run` gathers the session's context, then compresses it to fit
`handoff.hard_cap_tokens` (default 600). The context covers decisions, commits, changed
files and symbols, uncommitted and untracked files, and the last test run. `--print`
outputs only the payload, for piping; `--no-llm` skips the model. Writing the payload
into an agent's files is not available yet.

Compression is tried in order:

1. **Ollama** (`llm.model`, default `qwen2.5-coder:7b`), if it is running.
2. **Groq or OpenAI**, only if `llm.cloud_fallback` is `true` and `GROQ_API_KEY` /
   `OPENAI_API_KEY` is set. ContextBridge prints a notice whenever it uses the cloud,
   and only sanitized text is sent.
3. **A deterministic template**: always available and fully offline.

Token counts use tiktoken's `cl100k_base` encoding and are approximate for other
models. The encoding is downloaded once on first use. Offline, counts fall back to a
character-based estimate.

## Search

`cb search` does keyword search by default. Set `"vector": {"enabled": true}` in
`.context/config.json` and install the `vector` extra to get semantic search with
Chroma. Chroma's embedding model (about 80 MB) is downloaded on first use.
