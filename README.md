# ContextBridge

Local-first CLI (`cb`) that preserves project context across AI coding agents. It records
what you did (decisions, commits, changed functions, test runs), compresses it into a
short handoff, and writes it where the next agent (Claude Code, Cursor, Aider, ...) will
read it.

> Status: the command line and a desktop app are both built. See `contextbridge_spec.md` for
> the design and `docs/ARCHITECTURE.md` for how it works.

## The desktop app (no terminal needed)

If you'd rather click than type, there's a friendly desktop app with the same features:

- **Home**: where things stand, at a glance
- **Notes**: write down decisions, problems and to-dos
- **Changes**: the files and functions you've changed, and a button to run your tests
- **Hand off**: pick who's taking over (Claude Code, Cursor, or another tool), preview the
  summary in plain words, and write it
- **Settings**: AI on or off, automatic commit notes, and a clearly marked "start fresh" area

Open it by double-clicking [`launch.command`](launch.command) on macOS, or run `./launch.command`
from Terminal. See [LAUNCH.md](LAUNCH.md) for first-launch instructions. The app works fully
offline and never needs Ollama; if Ollama is unavailable, it uses a deterministic summary.
Artwork can be swapped without touching code: see [docs/GUI_ASSETS.md](docs/GUI_ASSETS.md).

## Offline guide and chatbot transfer

**Project guide** explains documented purpose, recorded progress, planned work, and rule-based suggestions. Its file table explains likely file roles and labels them as inferences.

**Chat bridge** works even without a project. Paste chat text or import a PDF/text file, pin key facts, preview a bounded excerpt, then copy it into another chatbot or save it as a PDF. The optional `browser-extension` exports selected text on your explicit click. These features require no model or subscription. See [docs/OFFLINE_BRIDGES.md](docs/OFFLINE_BRIDGES.md) for the workflow and limitations.

## Install

```bash
uv tool install .          # puts `cb` on your PATH
# or, from a checkout:
uv sync --all-extras && uv run cb --help
```

Requires Python 3.11+ and `git`. Optional extras: `gui` (the desktop app), `vector`
(semantic search), `cloud` (Groq/OpenAI), `build` (make the standalone app).

## 5-minute quickstart

```bash
cd your-project
cb init                                   # creates .contextbridge/, installs a git hook
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
| `cb init [--no-hooks] [--uninstall-hooks]` | Create `.contextbridge/`, add it to `.gitignore`, install the post-commit hook, start a session. Safe to repeat |
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
| `codex` | `AGENTS.md` | Adds a marked handoff block and preserves your existing instructions. Codex reads `AGENTS.md` automatically |
| `cursor` | `.cursor/rules/handoff.mdc` | An always-applied Cursor rule |
| `kiro` | `AGENTS.md` | Shares the marked handoff with Codex; Kiro also reads `AGENTS.md` automatically |
| `generic` (aliases `aider`, `opencode`) | `HANDOFF.md` | For Aider, OpenCode, or anything else: point the agent at this file |

`HANDOFF.md` and `handoff.mdc` are overwritten only if ContextBridge created them. If a
file with that name already exists, use `--force` to replace it. Codex and Kiro handoffs
share one marked block in `AGENTS.md`; the rest of your project instructions are preserved.
Because both agents load that file automatically, keep the handoff concise or replace it
when you switch sessions to avoid carrying stale context into every request.

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

`~/.config/contextbridge/config.json` (global) and `.contextbridge/config.json` (per project;
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

- Everything is stored locally in `.contextbridge/` (gitignored). Nothing leaves your machine
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

- **"No .contextbridge/ directory found"**: run `cb init` at the project root.
- **Commits are not being recorded**: check `cb init` did not report a skipped hook (an
  existing non-executable or non-shell `post-commit` hook is left untouched), and look at
  `.contextbridge/cb.log`. `cb hook post-commit` can be run by hand after a commit.
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

## Shared Python engine

The desktop app and CLI share `contextbridge.service.ProjectService` for project
operations. Notes, snapshots, keyword and semantic searches, commit capture,
handoffs, project maps, and guide explanations run through the same engine.
`ChatService` imports conversations and prepares offline chat handoffs without
an initialized project. The engine imports without Qt, Typer, or Rich.

```python
from pathlib import Path
from contextbridge.service import ProjectService

project = ProjectService.for_path(Path.cwd())
project.init(install_hook=False)
project.add_note("Use SQLite for local storage", "architecture")
snapshot = project.snapshot()
prepared = project.prepare_handoff("claude", "generic", use_llm=False)
print(prepared.handoff.payload)
```

Run the CLI with `uv run cb --help`, or launch the desktop app with
`uv run cb gui`. Run engine boundary checks with
`uv run pytest -q tests/test_engine_boundary.py tests/test_service.py`.

The next stages will add the watcher, optional provider setup, and local API.
The folder migration will copy existing `.contextbridge/` data and retain legacy reads.
The API will bind to `127.0.0.1` and authenticate every request with a random
per-session token. The first desktop packaging target is macOS, with signing deferred.

## Offline change watching and retained legacy storage

Initialize a project, then explicitly start watching in a terminal.

```bash
uv run cb init
uv run cb watch --agent codex --note "Working on the settings page"
```

Press Ctrl-C to flush pending changes and stop. Watching runs only for the lifetime
of this foreground command. Opening the desktop app or running another command
starts no watcher. `--seconds 60` provides a bounded run.

The defaults poll metadata once per second, save after two quiet seconds, and
close a continuing batch after 30 seconds. Tune them with `--interval`,
`--debounce`, and `--batch-window`. Repeated saves to a path share one record in
the batch. A batch includes its timestamp, observed event count, elapsed time,
paths, change types, size differences, and optional tool attribution and notes.
Commit capture records numeric added/removed line counts. Automatic capture
stores no source text, patch bodies, or function signatures. Existing saved
history is retained. Notes and handoffs remain user-requested text records.

The watcher prunes `.contextbridge/`, `.context/`, `.git/`, dependencies, virtual
environments, build and cache directories. It excludes `AGENTS.md`, `HANDOFF.md`,
`CLAUDE.md`, Cursor handoff output, `cb.log`, secret files, and symlinks. Root and
nested `.gitignore` rules apply, including negations. Source files are statted,
never opened. Change batches and their handoff-facing file records are committed
in one SQLite transaction. A restart establishes a fresh baseline.

Event counts describe eligible file transitions observed between polls. Several
saves between two scans can coalesce. Renames appear as a deleted path and an
added path in watch batches. Saves made while watching is stopped are outside
the watch session. Commit capture continues to be available through the existing
Git hook.

New projects use `.contextbridge/`. Existing `.context/` projects remain readable.
Running `cb init` copies the old folder into a temporary directory, uses SQLite
backup to include committed WAL data, verifies the copied database, and publishes
the complete `.contextbridge/` directory. The old folder is retained. Repeating
init preserves the current database. If a conflicting new folder exists without
a database, it is preserved and legacy discovery continues to work. Once both
folders contain databases, current storage takes precedence. Both folders are
added to `.gitignore`.

Run regression and burst checks with the development environment installed.

```bash
uv run pytest -q tests/test_watcher.py tests/test_workspace_migration.py
uv run pytest -q -s tests/test_watcher_bursts.py
```

Burst checks run actual Ruff formatting, Git checkout, Git pull from a local
repository, and offline `uv sync` installing a local wheel. Each source burst
changes 150 files. Measurements use `time.thread_time()` for the watcher thread
and wall time, reported as a percentage of one logical CPU. They exclude CPU
used by the commands creating the burst and by Git subprocesses. The tests print
scan, event, batch, and recorded-path counts for each run.

The macOS measurement run on 18 November 2026 produced these results with the
production defaults. Each observation window lasted approximately four seconds.

| Scenario | Observed changes | Batches | Watcher CPU, one core |
|---|---:|---:|---:|
| Idle, 150 source files | 0 | 0 | 2.54% |
| Ruff format, 150 files | 150 | 1 | 3.17% |
| Git checkout, 150 files | 150 | 1 | 3.14% |
| Git pull from local repository, 150 files | 150 | 1 | 3.07% |
| Offline uv sync, 150 installed modules | 0 | 0 | 0.30% |

These are small-fixture measurements. CPU use varies with project size and ignore
rules. Increase `--interval` to reduce scan frequency for larger projects.
