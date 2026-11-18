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
- **Settings**: offline, Ollama or API selection, automatic commit notes, and a clearly marked "start fresh" area

Open it by double-clicking [`launch.command`](launch.command) on macOS, or run `./launch.command`
from Terminal. See [LAUNCH.md](LAUNCH.md) for first-launch instructions. The app works fully
offline and never needs Ollama; if Ollama is unavailable, it uses a deterministic summary.
On first launch, setup explains offline operation and lets you continue without a model,
choose local Ollama, or enter an API endpoint and key. Connection checks run only when clicked.
Each check sends a tiny test prompt to the exact model and endpoint entered in the form,
with no project context. Checks allow 64 output tokens for ordinary models and 1024
completion tokens for recognized reasoning models. GPT-OSS on Groq uses low reasoning
effort, reads only final content and reports a clear failure if no final answer is returned.
A successful check does not save
the form; click **Save model settings** to apply it. Missing-model errors show the selected
model, attempted URL and offered models (or explain why the model list is unavailable).
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

Requires Python 3.11+ and `git`. Optional extras: `api` (the local REST API + dashboard),
`mcp` (the MCP server), `gui` (the desktop app), `vector` (semantic search), `cloud`
(legacy SDK tooling), `build` (make the standalone app).

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
| `cb serve [--port 8765]` | Serve the REST API and the web dashboard on `127.0.0.1`, printing a one-session URL with its token |
| `cb mcp` | Serve the MCP tools (`get_project_status`, `log_decision_or_todo`, ...) to coding agents over stdio |
| `cb configure --provider none\|ollama\|openai-compatible [--global]` | Explicitly choose a model provider or offline operation |
| `cb provider show` / `cb provider test` | Show effective settings without reading the key; test the selected model with a tiny prompt |
| `cb summarize [--budget 2000] [--no-llm] [--refresh]` | Build a project overview and cache grounded per-file summaries in SQLite |
| `cb todo list [--all]` / `cb todo resolve ID` / `cb todo dismiss ID` / `cb todo supersede OLD_ID NEW_ID` | Retain history while removing completed, obsolete or superseded items from next steps |
| `cb handoff --to B [--from A] [--budget N] [--copy] [--dry-run] [--no-llm] [--print] [--force]` | Build, compress and write the handoff |
| `cb search "query"` | Search decisions and changes (keyword, or semantic with the `vector` extra) |
| `cb clear [--session] [--all] [--cleanup-adapters] [--yes]` | Archive the session, delete all data, and/or remove injected agent files |

Global options: `--repo PATH`, `--verbose`. Exit codes: `0` ok, `1` user error,
`2` environment error (no git, not initialized), `3` LLM failure with no fallback.

Optional shell alias for test runs: `alias t='cb run -- pytest'`.

## Dashboard, API and MCP server

Three ways to read the same state, all of them deterministic (0 tokens):

```bash
cb serve      # REST API + web dashboard on http://127.0.0.1:8765/ui/
cb mcp        # stdio MCP server for coding agents
```

### Web dashboard

Build it once, then serve:

```bash
cd frontend && npm install && npm run build     # writes frontend/dist
cb serve                                        # prints http://127.0.0.1:8765/ui/#token=...
```

The URL already carries the session token in its fragment, so opening it is enough —
browsers never send fragments to the server, and every `/v1/*` call still authenticates
with `Authorization: Bearer <token>`. Without a built dashboard, `cb serve` prints the
token on its own line instead.

Three tabs:

| Tab | Contents |
|---|---|
| **Overview & Timeline** | The persistent North Star intent (one row in SQLite) and the last 15 commits, read from `git log` + `git diff --stat` when the server boots — no manual command |
| **To-Do & Tasks** | A checklist that writes straight to SQLite, plus the Chat Bridge box: paste a transcript and it becomes tasks using fixed parsing rules (checkboxes, `TODO:`/`ACTION:` lines, bullets under a *Next steps* heading) |
| **Architecture Map** | React Flow rendering of the same deterministic AST graph JSON the desktop app uses (`GET /v1/graph`), with a Mermaid copy button |

| Endpoint | Purpose |
|---|---|
| `GET /v1/dashboard` | Intent, task counts, commits and momentum in one call |
| `GET` / `PUT /v1/intent` | Read or replace the North Star |
| `GET` / `POST` / `PATCH` / `DELETE /v1/tasks` | The checklist (`?status=`, `?source=`) |
| `GET /v1/commits[?refresh=true]` | The momentum window |
| `POST /v1/chat-import` | `commit: false` previews transcript tasks, `true` stores them |
| `GET /v1/graph`, `GET /v1/mermaid` | Deterministic import graph |

The Vite dev server (`npm run dev`, port 5173) proxies `/v1` to `127.0.0.1:8765`.

### MCP server

```json
{
  "mcpServers": {
    "contextbridge": { "command": "cb", "args": ["mcp"], "cwd": "/path/to/your/project" }
  }
}
```

Tools: `get_project_status`, `log_decision_or_todo`, `set_project_intent`, `list_tasks`,
`get_git_history`, `get_architecture_graph`. Install the extra with `uv sync --extra mcp`.

### The determinism rule

Parsing, AST generation and timeline extraction are 100% deterministic and cost 0 tokens.
Model calls are reserved for the optional natural-language file explanations, which are
cached in SQLite and never required to render any tab.

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

### Project summaries, including fresh repositories

```bash
cb summarize --no-llm --budget 2000
cb summarize --budget 2000
cb summarize --refresh --budget 2000
```

Summarization works without notes or prior commits. It creates local storage when
necessary, without installing hooks. Offline analysis reads the eligible file tree,
README, dependency manifests (including optional GUI extras), declared entry points,
Python AST/tree-sitter symbols and imports, and recent Git history. Source is parsed
as data and never executed. Unparseable or unsupported files receive conservative
static descriptions. Tests appear as counts, without test function listings.

Model assistance selects at most eight ranked source files. Entry points, core modules
and meaningful change sizes outrank other files. Each request contains one sanitized
chunk with a 2500-token prompt check. New chunk plans start at no more than 1200
characters (within the configured ceiling) and shrink further for token-per-minute
limits. Cached legacy plans retain their boundaries so completed work stays reusable. It never
constructs a whole-repository prompt. The model returns JSON with `purpose`,
`key_symbols` and `depends_on`. To prevent invented facts, purposes are extractive
selections from supplied documentation or static descriptions; symbols and imports
must be present in the supplied evidence. Invalid JSON or ungrounded facts retry once.
Chunk results merge locally, without another model call.

SQLite caches file and partial-chunk summaries by source SHA-256, analysis version,
provider/model/endpoint and chunk settings. Unchanged successful chunks are reused;
changed content invalidates its old labels. `--refresh` bypasses reuse for this run,
while retaining request limits. `Database.file_summaries()` exposes derived purposes,
symbols and imports for future diagrams. Source chunks and prompts are never stored.

Hosted transfers are announced before the first request. Sensitive files, secret
folders, symlinks, configured ignores and nested Git ignore rules are excluded;
prompts and stored descriptions are sanitized. The scanner defaults to 600 paths,
256 KB per analyzed file and 8 MB total source. Limits are reported when reached.
Generation requests default to 12 per run, spaced at least 0.5 seconds apart. Success
and failure responses both update pacing from `x-ratelimit-limit-*`, `remaining-*`,
`reset-*` and `Retry-After` headers. Groq's requests header is treated as a daily limit;
token headers pace token usage per minute, including an output allowance. HTTP 429
retries use exponential backoff and honor the longer server wait. Wait times are shown.
A single wait over 60 seconds, more than 120 seconds of waiting per run, or repeated
rate limiting stops model work and keeps offline descriptions. Limits and partial
chunk progress are persisted; rerunning resumes without repeating successful chunks
or ignoring an outstanding cooldown. Missing keys, timeouts and provider failures also
preserve offline output. Progress goes to stderr, using the same analyzed-file total
as Analysis. Folder counts separately identify all eligible files and analyzed files.

Optional beginner explanations are enabled with `cb summarize --explain`, or
`summary.explanations: true`. Each contains 1-2 sentences and is labeled **AI-written**.
They are stored separately in SQLite (`Database.file_explanations()`), never substituted
for grounded purposes. The model must mark code references with backticks; named files,
functions/classes, qualified identifiers and declared references are checked against
that file's symbols/imports. Invalid explanations are dropped. Valid ones are cached
by content hash and provider profile, and invalidated when the source changes. They
are available as optional labels for Step 6.

Configurable bounds in global or project config:

```json
{"summary": {"max_requests": 12, "max_files": 600, "chunk_chars": 4000, "request_interval": 0.5}}
```

Handoffs use offline or matching cached model descriptions for Goal and the prioritized
reading list. With fewer than four logged notes, they also include **What each part
does**. Generating a handoff does not initiate repository model analysis; run
`cb summarize` explicitly to populate the model cache. Optional handoff wording
polishing is **off by default**, including dry runs. Use `cb handoff --polish`, check
**Polish handoffs with model (uses quota)** in GUI Settings, or set `handoff.polish: true`.
`--no-llm` overrides that setting. Default handoffs use no model requests. Only the latest
completion note in the active session has full detail; older work uses one-line
summaries. Resolved todos are omitted from Done, commits are capped at three, duplicate
entries are suppressed and dismissed/superseded items are omitted. The summary shows a
folder-level inventory instead of an alphabetical list of files.

For a Groq key already saved in the GUI, enable the same endpoint and your chosen model
without entering the key again:

```bash
cb configure --provider openai-compatible --model openai/gpt-oss-120b \
  --endpoint https://api.groq.com/openai/v1
cb provider show
cb provider test
cb summarize --budget 2000
cb handoff --to codex --dry-run
```

Use `cb todo list` to find logged IDs, then `cb todo resolve dec_...` for completed work
or `cb todo dismiss dec_...` for obsolete work. `cb todo supersede OLD_ID NEW_ID` also
works for note IDs, so revised completion claims can explicitly replace older ones.
History stays available through `cb todo list --all` and the database.

`cb handoff` gathers the session's decisions, commits, changed files and symbols,
uncommitted and untracked files, and the last test run. It then fits that to
`handoff.hard_cap_tokens` (default 2000, overridden by `--budget`). It reserves space
for Goal, Done, Decisions and Next steps before filling file details. Source files
rank above tests, which appear as aggregate counts.

The default provider is **none**. Optional providers can polish the recorded wording
while preserving facts, file paths, numbers and line order. Missing models, bad responses,
timeouts and rate limits fall back to the offline template. Choosing Ollama does not
enable a hosted fallback. Existing explicit Groq/OpenAI configurations and the old
`cloud_fallback` opt-in remain supported.
Token counts use tiktoken's `cl100k_base` and are approximate for other models. The
payload can be larger than the raw context for a very small session; the ratio shown is
always computed from real counts.

## Configuration

`~/.config/contextbridge/config.json` (global) and `.contextbridge/config.json` (per project;
wins). Example:

```json
{
  "llm": { "provider": "none", "num_ctx": 16384 },
  "capture": { "max_diff_chars_per_file": 4000, "ignore_globs": ["*.lock", "dist/**"] },
  "handoff": { "target_tokens": 2000, "hard_cap_tokens": 2000 },
  "vector": { "enabled": false }
}
```

Choose a provider in desktop Settings or use the CLI. Omit `--global` to save the
selection for the current initialized project.

```bash
cb configure --global --provider none
cb configure --global --provider ollama --model qwen2.5-coder:7b
cb configure --global --provider openai-compatible --model YOUR_MODEL \
  --endpoint https://YOUR_PROVIDER/v1 --set-key
```

`--set-key` uses a hidden prompt. Keys are stored in the OS keyring, scoped to the
endpoint, never in configuration or the database. If a secure OS keyring is unavailable,
setup reports the error and offline mode stays available. Legacy `OPENAI_API_KEY` and
`GROQ_API_KEY` settings still work; `CB_API_KEY` is an explicit environment fallback for
compatible endpoints. The provider selection must also be enabled.

Ollama uses loopback HTTP with an explicit `options.num_ctx` (default 16384, configurable
as `llm.num_ctx`). Compatible endpoints use `/chat/completions` with optional JSON schema
output. Requests go directly to the selected endpoint, ignoring proxy environment
variables and redirects. No Ollama, OpenAI or Groq SDK is required.

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

The watcher and optional provider setup are implemented. The next stages add the local API.
The folder migration copies existing `.context/` data and retains legacy reads.
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

## Structured handoffs and chat imports

Project handoffs use Goal, Done, Decisions, Current state, Open problems,
Next steps, and Files to read first. Chat imports use the same core sections
and a Files section for mentioned filenames. The renderer
reserves space for essential context before adding file details. Each section
contains evidence or an explicit statement that nothing was logged.

The default output budget is approximately 2,000 tokens. Override it for one
handoff with `--budget`, or set `handoff.target_tokens` and
`handoff.hard_cap_tokens` in `.contextbridge/config.json`. Existing explicit
project settings continue to apply. Token counts use the app's tokenizer or its
offline character estimate.

```bash
cb handoff --to codex --no-llm
cb handoff --to cursor --budget 1500 --dry-run
cb handoff --copy --no-llm
cb handoff --to codex --copy --no-llm
```

`--from` is optional. The engine uses an explicit value, `CB_AGENT`, a detected
Codex/Claude environment, or `unknown`. A previous destination is never used
as evidence of the current agent. Matching source and destination agents produce
a warning. `--dry-run`
previews the payload. `--copy` without a destination copies the payload for a
chatbot. Clipboard support uses macOS, Windows, or an installed Linux clipboard
utility. Existing adapters retain their marker and ownership checks.

Goal comes from the latest `Goal:` note, the project's package description, or
the first README paragraph. Recorded notes, commits, and change records supply
Done. Architecture notes supply Decisions. Todo notes and `Next:` notes supply
Next steps. The reading list contains at most eight files. Declared entry points, core modules,
and substantial changes receive priority, with deterministic ties. Known one-line
edits and deleted files are excluded. Live change magnitude comes from full
in-memory diffs, independently of display snippet limits. Short descriptions use
declared entry points and module-name hints. Per-file summaries include recorded
line or size differences. Optional GUI dependencies are included in the tech stack.
Tests appear as counts, with the last run's age and a stale marker when a source
file changed afterward. The engine retains timestamp precision for same-second
edits and test runs.

```bash
cb log "Goal: Help students transfer project context between coding tools"
cb log "Done: Added offline batch capture"
cb log "Keep the core independent of the UI" --tag architecture
cb log "Add provider selection" --tag todo
```

Import a chat without initializing a project. The command reads stdin by default
and writes Markdown to stdout. File and clipboard input are also available.

```bash
cb import-chat --file conversation.txt --budget 2000
cb import-chat --clipboard --copy
cat conversation.txt | cb import-chat --goal "Continue the implementation"
```

Offline chat condensation parses User/Assistant, User prompt/Response, and
You said/ChatGPT said exports into turns. Wrapped prose is joined before
extraction. Goal preserves the first substantial user request. Current state
contains the latest user request and a one-line extract from the latest assistant
response. Done records completion claims in chronological order. Next steps
contains explicit ending instructions or final open questions. URLs, tool output,
code blocks, and repeated export lines are filtered. Mentioned filenames appear
under Files. Chat budgets retain complete statements or show a complete budget
notice. Sentences are never cut into partial excerpts.

Inputs shorter than approximately 100 words or lacking recognized speaker turns
produce a warning. Unstructured prose is labeled as imported text, and explicit
labels can still supply facts. Secrets are redacted before extraction. Input is
limited to 100,000 characters. The desktop Chat bridge uses the same parser and
shows input warnings. Clipboard output is labeled as a copied handoff.

The realistic Flutter fixture covers requests for lab observations, project
creation steps, procedures, HTML output simulations, and later navigation and
state-management exercises.

```bash
uv run cb import-chat --file tests/fixtures/flutter_lab_chat.txt --budget 2000
uv run pytest -q tests/test_chat_turns.py tests/test_handoff_followup.py
```

The deterministic document is complete without a model. Explicitly opted-in model polishing
receives only the selected handoff lines. Accepted responses preserve every word,
path, number, negation, and line order, allowing spacing and final punctuation
changes. Invalid responses retry once and fall back to the deterministic document.
This intentionally limits model rewriting so factual wording remains verifiable.

```bash
uv run pytest -q tests/test_handoff_quality.py tests/test_compressor.py
```

## Project explainer prototype (Step 6)

The overview groups source files into five to eight folder-based components where
there are enough real folders (small projects can have fewer). Cards show a plain
name, a short description and the analyzed file count. Root-level source files
appear as **Project root**. Unexplained cards say **Not explained yet** and offer
an **Explain** button. Folder descriptions never come from a folder name: files
are explained first, then their explanations are reduced into an **AI-written**
component summary. Partial coverage is shown as a file/explained count. File changes
or changed explanation inputs invalidate the component summary.
Overview arrows come only from detected local imports, never guesses from folder
names. Missing connections are explained rather than invented.

Import parsing supports Python, Dart/Flutter `package:` imports within the current
package and relative imports, plus local JavaScript/TypeScript ES imports and
`require`. TS/JS resolves `baseUrl`, wildcard `paths` aliases and index files from
`tsconfig.json` or `jsconfig.json`, including bounded local config inheritance.
Configs outside the project and package-based inheritance are skipped. Flutter package names come from `pubspec.yaml`; `lib/main.dart` is the
startup file. Python entry points come from package scripts or main/app/server
files; JavaScript/TypeScript uses package `main`/`bin` and index/main files. Other
languages currently show file boxes without import/call parsing. Only Python calls
are resolved. A flow with no detected startup file explains that limitation and
lets you choose any starting file; a project with no source files shows an empty
state with guidance.

File detail says **Not explained yet** when neither documentation nor a useful
cached description exists. **Explain this file** uses the selected provider only
after a privacy confirmation. It sends a small redacted excerpt of that file and
its names, validates the response, labels it **AI-written**, and caches it by file
content and provider/model. Unchanged files reuse that cache; changing the file
invalidates the explanation. Choose a provider in Settings first. Refreshing or
opening a diagram never starts a model request. Uses/Used by appear only when
connections were detected.

**Explain project** processes eligible files by importance without the eight-file
limit used by regular summarization. Its privacy dialog lets you choose a request
budget (1–200, default 20) and a reserved-token budget (default 40,000). Each request
reserves estimated prompt/schema input plus maximum output tokens; this is a
conservative estimate, not a billing meter. File explanations, folder reductions,
invalid-JSON retries and provider requests share those budgets. Progress shows file
positions, cache reuse, requests and reserved tokens. Requests are paced using the
provider's rate headers and configured spacing. A rate limit stops the action with
a retry time and an explicit cache/resume message. Run it again to continue;
unchanged files and completed reduction chunks are reused. **Explain component**
uses the same dialog and pipeline for only that component's files. Ordinary graph
refreshes never generate explanations.

Folder reductions receive validated file explanations, never source code, and use
the same file/function/class-name validation as file explanations. Large groups
are reduced in small cached batches. Invalid names are rejected. The existing
inventory and file-size safety bounds still apply; request/token exhaustion leaves
all completed progress cached.

Next.js app-router files are named by URL convention: `app/admin/page.tsx` becomes
**Admin page (/admin)**, `app/layout.tsx` becomes **Root layout (/)**, and
`app/api/events/route.ts` becomes **API route (/api/events)**. Route groups and
parallel-slot folders are omitted from displayed URLs. Flutter `lib/main.dart` is
labeled as startup. Literal Express `app`/`router` routes and Flask decorators are
shown on their files. Dynamic routes assembled in code and router mount prefixes
are outside this prototype's route detection.

The canvas uses the app's cream theme. Explanations and counts appear inside the
cards; default scaling stays readable, with scrolling for larger graphs.

The API, extra views and Mermaid refinement are deferred during this prototype
iteration. The existing optional interfaces below remain available.

The existing **Project guide → Flowcharts** page now uses the same graph JSON as
our local API. Open a project with `uv run cb gui`, choose **Refresh guide**, then
select **Project overview**, **Inside one file**, or **How a request travels**.
The file selector chooses a file or a starting point. Click a box for its purpose,
related files, what it uses and what uses it. **Open this part** opens file detail;
**Expand** increases the node limit. **Copy as Mermaid** copies a deterministic
diagram for a fenced `mermaid` block in Markdown.

Graphs work offline and never ask a model to invent connections. Current cached
per-file purposes and optional beginner explanations from `cb summarize --explain`
are reused only while the file's content hash matches. Beginner explanations
remain separately labeled **AI-written**. Refreshing a graph or reading API data
does not call a model or read API keys.

The engine is independent of Qt and FastAPI. Nodes have stable IDs, kind, relative
path, folder group, grounded purpose and optional AI-written text. Edges represent
local imports and statically resolved calls. Python files expose classes/functions;
other supported source files currently expose file nodes. The request view shows
possible routes, not a recording of an executed request: dynamic dispatch and
non-Python internals may be missing. Scans read at most 180 source files, 200
folders and approximately 2 MB, with a 64 KiB per-file limit. Ignored, hidden,
secret-related and linked files are excluded. Views allow 2–60 nodes (12 in the
embedded GUI, 24 expanded/API by default), with **+N more** for omitted nodes.
Scan and edge limits are reported in graph metadata.

Install and start the optional server explicitly:

```bash
uv sync --extra api --extra gui --extra dev
uv run cb serve --port 8765
```

It binds exclusively to `127.0.0.1`. Startup prints a new random session token;
restarting invalidates the old token. Every route and method requires
`Authorization: Bearer <token>`, including the API schema. Tokens are never
accepted in URLs. There is no unauthenticated docs page or CORS exception.
Keep the token local. In another terminal, read it without saving it to shell history:

```bash
read -rs 'CB_SESSION_TOKEN?Paste session token: '; echo
curl -H "Authorization: Bearer $CB_SESSION_TOKEN" http://127.0.0.1:8765/v1/status
curl -H "Authorization: Bearer $CB_SESSION_TOKEN" 'http://127.0.0.1:8765/v1/graph?view=overview'
curl -H "Authorization: Bearer $CB_SESSION_TOKEN" 'http://127.0.0.1:8765/v1/graph?view=module&module=contextbridge/service.py'
curl -H "Authorization: Bearer $CB_SESSION_TOKEN" 'http://127.0.0.1:8765/v1/graph?view=flow&module=contextbridge/main.py&limit=12'
curl -H "Authorization: Bearer $CB_SESSION_TOKEN" 'http://127.0.0.1:8765/v1/mermaid?view=flow&module=contextbridge/main.py' > request-flow.mmd
curl -H "Authorization: Bearer $CB_SESSION_TOKEN" http://127.0.0.1:8765/v1/summary
curl -H "Authorization: Bearer $CB_SESSION_TOKEN" http://127.0.0.1:8765/v1/openapi
curl -H "Authorization: Bearer $CB_SESSION_TOKEN" -H 'Content-Type: application/json' \
  -d '{"to_agent":"codex","budget":2000}' http://127.0.0.1:8765/v1/handoffs/preview
unset CB_SESSION_TOKEN
```

These graph file paths are for ContextBridge itself; choose paths from
`meta.available_modules` and `meta.entrypoints` for another project. POST to
`/v1/handoffs` with the same body to write the destination handoff. Preview may
initialize local project state, but does not write an adapter handoff. Handoffs
are deterministic by default; only an explicit `"polish": true` requests model
polishing. Stop the server with **Ctrl-C**. It never starts in the background.

Validation commands:

```bash
uv run pytest -q tests/test_explainer_graph.py tests/test_local_api.py tests/test_engine_boundary.py
uv run pytest -q tests/test_flutter_explainer.py tests/test_next_explainer.py
uv run pytest -q tests/test_gui.py -k 'guide or explainer or flutter or explain_button'
uv run ruff check contextbridge tests
uv run mypy contextbridge
uv run cb run -- uv run pytest -q
```
