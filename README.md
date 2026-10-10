# ContextBridge

Local-first CLI (`cb`) that preserves project context across AI coding agents. It records
what you did (decisions, commits, changed functions, test runs), compresses it into a
short handoff, and writes it where the next agent (Claude Code, Cursor, Aider, ...) will
read it.

> Status: all four phases are built. See `contextbridge_spec.md` for the design.

## Install

```bash
uv tool install .          # puts `cb` on your PATH
# or, from a checkout:
uv sync --all-extras && uv run cb --help
```

Requires Python 3.11+ and `git`. Optional extras: `vector` (semantic search),
`cloud` (Groq/OpenAI).

## 5-minute quickstart

```bash
cd your-project
cb init                                   # creates .context/, installs a git hook
cb log "Prices are integer cents" --tag architecture
cb log "Empty cart crashes checkout" --tag blocker
# ...work, commit as usual: the hook records changed files and functions...
cb run -- pytest -q                       # runs tests and records the result
cb status                                 # see what has been captured
cb handoff --from claude --to cursor      # write the handoff for the next agent
```

No LLM is needed. Without one, the handoff is built from a deterministic template.

## Commands

| Command | What it does |
|---|---|
| `cb init [--no-hooks] [--uninstall-hooks]` | Create `.context/`, add it to `.gitignore`, install the post-commit hook, start a session. Safe to repeat |
| `cb log "msg" [--tag note\|bug\|architecture\|blocker\|todo]` | Record a note in the current session |
| `cb run -- <cmd>` | Run a command, stream its output, record pytest/jest/vitest/`go test`/`cargo test` results |
| `cb status` | Dashboard: branch, changes and symbols, tests, decisions, last handoff, token stats |
| `cb handoff --from A --to B [--dry-run] [--no-llm] [--print] [--force]` | Build, compress and write the handoff |
| `cb search "query"` | Search decisions and changes (keyword, or semantic with the `vector` extra) |
| `cb clear [--session] [--all] [--cleanup-adapters] [--yes]` | Archive the session, delete all data, and/or remove injected agent files |

Global options: `--repo PATH`, `--verbose`. Exit codes: `0` ok, `1` user error,
`2` environment error (no git, not initialized), `3` LLM failure with no fallback.

Optional shell alias for test runs: `alias t='cb run -- pytest'`.

## Per-agent setup

`cb handoff --to <agent>` writes one file. Running it again replaces the previous block;
it never appends a second one.

| `--to` | File written | Notes |
|---|---|---|
| `claude` (alias `claude-code`) | `CLAUDE.md` | Adds a `<context_handoff>` block; your own content is untouched. Claude Code reads `CLAUDE.md` automatically |
| `cursor` | `.cursor/rules/handoff.mdc` | An always-applied Cursor rule |
| `generic` (aliases `aider`, `opencode`) | `HANDOFF.md` | For Aider, OpenCode, or anything else: point the agent at this file |

`HANDOFF.md` and `handoff.mdc` are overwritten only if ContextBridge created them. If a
file with that name already exists, use `--force` to replace it.

`--from` is just a label (any name). Remove everything ContextBridge injected, leaving the
rest of your files as they were:

```bash
cb clear --cleanup-adapters
```

The injected files are ordinary working-tree changes. Commit or `.gitignore` them as you
prefer. ContextBridge ignores its own injected content when it summarizes your changes,
so handoffs do not grow on each other.

## How compression works

`cb handoff` gathers the session's decisions, commits, changed files and symbols,
uncommitted and untracked files, and the last test run. It then fits that to
`handoff.hard_cap_tokens` (default 600), trying in order:

1. **Ollama** (`llm.model`, default `qwen2.5-coder:7b`), if it is running.
2. **Groq or OpenAI**, only if `llm.cloud_fallback` is `true` and `GROQ_API_KEY` /
   `OPENAI_API_KEY` is set. A notice is printed every time the cloud is used.
3. **A deterministic template**: always available and fully offline.

Under a tight cap the lowest-priority content is dropped first: notes, then diffs,
todos, changed symbols, failing tests, architecture decisions, and finally blockers.
Token counts use tiktoken's `cl100k_base` and are approximate for other models. The
payload can be larger than the raw context for a very small session; the ratio shown is
always computed from real counts.

## Configuration

`~/.config/contextbridge/config.json` (global) and `.context/config.json` (per project;
wins). Example:

```json
{
  "llm": { "provider": "ollama", "model": "qwen2.5-coder:7b", "cloud_fallback": false },
  "capture": { "max_diff_chars_per_file": 4000, "ignore_globs": ["*.lock", "dist/**"] },
  "handoff": { "target_tokens": 400, "hard_cap_tokens": 600 },
  "vector": { "enabled": false }
}
```

API keys are read only from environment variables and are never written to config or the
database.

## Privacy

- Everything is stored locally in `.context/` (gitignored). Nothing leaves your machine
  unless you opt in to a cloud provider.
- Every piece of text passes through the sanitizer before it is stored, logged, put in a
  prompt, or written to an agent file. It redacts cloud and API keys, tokens, JWTs,
  private keys, credentials in URLs, `.env`-style secrets and high-entropy strings.
- `.env*`, `*.pem`, `*.key` and `id_rsa*` files are skipped entirely.
- With a cloud provider enabled, only the sanitized context is sent, and you are told
  each time. The sanitizer is pattern-based: it reduces the risk, it cannot guarantee
  that no secret slips through. Do not log secrets on purpose.
- `cb clear --all` deletes every session and the vector index.

## Troubleshooting

- **"No .context/ directory found"**: run `cb init` at the project root.
- **Commits are not being recorded**: check `cb init` did not report a skipped hook (an
  existing non-executable or non-shell `post-commit` hook is left untouched), and look at
  `.context/cb.log`. `cb hook post-commit` can be run by hand after a commit.
- **The hook runs the wrong `cb`**: the hook uses the Python that ran `cb init` first.
  Re-run `cb init` from the environment you want.
- **"Ollama is not reachable"**: expected when it is not running; the deterministic
  summary is used. Start Ollama and `ollama pull qwen2.5-coder:7b` for LLM summaries.
- **First `cb status` or `cb handoff` is slow offline**: tiktoken downloads its encoding
  once. After 5 seconds ContextBridge falls back to an estimate and labels counts
  "approx.".
- **"Unbalanced ContextBridge markers"**: a `contextbridge:start` / `end` line was edited
  or deleted by hand. Fix or remove the stray line; ContextBridge will not guess.
- **`HANDOFF.md already exists`**: it was not created by ContextBridge. Move it, or pass
  `--force`.
- **Semantic search**: install the `vector` extra and set `"vector": {"enabled": true}`.
  The first search downloads Chroma's embedding model (about 80 MB); until then `cb search`
  uses keyword search and says so.

## Development

```bash
uv sync --all-extras
uv run ruff check . && uv run ruff format --check . && uv run mypy contextbridge
uv run pytest -q
```

Tests use temporary git repositories. LLM calls are mocked; nothing needs a network or a
running Ollama.
