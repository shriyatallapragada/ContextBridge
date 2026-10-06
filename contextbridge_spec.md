# ContextBridge: Master Specification & Implementation Blueprint (v1.1)

> **Notice for AI coding agent (Claude Code, Cursor, OpenCode, Aider, etc.):**
> You are building **ContextBridge**, a local-first CLI that preserves project context (decisions, file-touch maps, symbol-level changes, test results) and hands it off between AI coding agents. Follow the architecture, schemas, acceptance criteria and phases below. Build modular, typed, tested code. Work **one phase at a time** and stop after each phase for review. Do not start the next phase until the acceptance criteria for the current one pass.

---

## 1. Mission

When developers switch AI coding agents or hit rate limits, conversation state is lost and the next agent re-reads the repo, burning tokens. ContextBridge is a persistent, local memory layer that:

1. **Captures** git diffs, modified symbols (Tree-sitter), test/command results, and manual notes.
2. **Stores** them locally: SQLite for structured data, ChromaDB for optional semantic search.
3. **Compresses** raw context into a dense handoff of roughly 300-500 tokens, using a local LLM (Ollama) or an opt-in cloud API, with a deterministic no-LLM fallback.
4. **Injects** the handoff into the target agent's context file (`.cursor/rules/handoff.mdc`, `CLAUDE.md`, `HANDOFF.md`).

**Design principles**
- **Local-first and private:** nothing leaves the machine unless the user explicitly enables a cloud provider.
- **Works with zero setup:** `cb init` then `cb handoff` must work with no LLM and no vector DB installed (deterministic fallback).
- **Non-invasive:** hooks never block commits; injection is idempotent and reversible.
- **Honest metrics:** report measured token counts, not marketing numbers. "15,000 to 400 tokens" is a *target*, not a guarantee.

**Non-goals for v1:** cloud sync, team sharing, a GUI, capturing live chat transcripts from agents.

---

## 2. Technical Stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Fully type-hinted, `mypy --strict` clean on `core/` and `storage/` |
| CLI | `typer` + `rich` | Console script entry point `cb` |
| Structured storage | `sqlite3` (stdlib) | WAL mode, `PRAGMA foreign_keys=ON`, versioned migrations. Sync API is enough; do not add `aiosqlite` |
| Vector store | `chromadb` (embedded, persistent) | **Optional extra** (`pip install contextbridge[vector]`). Heavy dependency; the core must work without it |
| Code parsing | `tree-sitter` + **`tree-sitter-language-pack`** | `tree-sitter-languages` is unmaintained and breaks on newer Python; do not use it. Fallback to regex symbol detection if a grammar is unavailable |
| Token counting | `tiktoken` (`cl100k_base`) | Counts are an approximation for non-OpenAI models; label them "approx." in the UI |
| Local LLM | `ollama` Python client | Default model `qwen2.5-coder:7b`, configurable |
| Cloud LLM (opt-in) | `openai` / `groq` SDKs | Optional extra `contextbridge[cloud]`. Disabled by default |
| Packaging | `uv` (preferred) or `poetry` | `pyproject.toml` with extras: `vector`, `cloud`, `dev` |
| Testing | `pytest`, `pytest-cov`, `typer.testing.CliRunner` | Git tests use temporary repos (`tmp_path`) |
| Lint/format | `ruff`, `mypy` | Configured in `pyproject.toml` |

Removed from the original draft: **Mem0** (redundant with Chroma, adds complexity). Revisit in v2.

---

## 3. Directory Structure

```
contextbridge/
├── pyproject.toml
├── README.md
├── contextbridge/
│   ├── __init__.py
│   ├── main.py               # Typer app, registers all commands (`cb`)
│   ├── config.py             # Config load/save, defaults, env overrides
│   ├── errors.py             # Typed exceptions (NotInitializedError, NotAGitRepoError, ...)
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── init_cmd.py       # cb init
│   │   ├── log_cmd.py        # cb log
│   │   ├── run_cmd.py        # cb run -- <command>   (captures test/command output)
│   │   ├── hook_cmd.py       # cb hook post-commit   (internal; called by git hook)
│   │   ├── handoff_cmd.py    # cb handoff
│   │   ├── status_cmd.py     # cb status
│   │   ├── search_cmd.py     # cb search "query"     (needs [vector]; falls back to LIKE search)
│   │   └── clear_cmd.py      # cb clear
│   ├── core/
│   │   ├── git_observer.py   # status, commits, diffs, branch, hook install/uninstall
│   │   ├── shell_listener.py # parse pytest/jest/go test/cargo test output
│   │   ├── ast_parser.py     # Tree-sitter symbol extraction + diff-to-symbol mapping
│   │   └── sanitizer.py      # secret redaction
│   ├── storage/
│   │   ├── db.py             # schema, migrations, queries
│   │   ├── vector_store.py   # Chroma client, embedding (optional)
│   │   └── workspace.py      # .context/ folder + .gitignore management
│   ├── synthesis/
│   │   ├── context_builder.py# assembles raw context within a size budget
│   │   ├── compressor.py     # LLM compression + deterministic fallback
│   │   ├── llm_client.py     # Ollama / Groq / OpenAI router
│   │   └── token_counter.py  # tiktoken wrapper
│   └── adapters/
│       ├── base_adapter.py
│       ├── cursor_adapter.py
│       ├── claude_adapter.py
│       ├── generic_adapter.py
│       └── registry.py       # name -> adapter lookup (e.g. "claude", "cursor", "generic")
└── tests/
    ├── conftest.py           # tmp git repo fixture
    ├── test_sanitizer.py
    ├── test_db.py
    ├── test_git_observer.py
    ├── test_ast_parser.py
    ├── test_shell_listener.py
    ├── test_compressor.py
    ├── test_adapters.py
    └── test_cli_e2e.py
```

---

## 4. Data Model

### 4.1 SQLite (`.context/state.db`)

Use `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;`. Store a `schema_version` in `PRAGMA user_version` and apply migrations in order. IDs are `prefix_<uuid4 hex[:12]>` (e.g. `sess_a1b2c3d4e5f6`). Timestamps are stored in **UTC ISO-8601**.

```sql
CREATE TABLE IF NOT EXISTS sessions (
  id           TEXT PRIMARY KEY,
  branch_name  TEXT NOT NULL,
  start_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  status       TEXT CHECK(status IN ('active','archived','handed_off')) DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS decision_logs (
  id         TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  content    TEXT NOT NULL,                  -- already sanitized
  tag        TEXT CHECK(tag IN ('note','bug','architecture','blocker','todo')) DEFAULT 'note',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS file_changes (
  id               TEXT PRIMARY KEY,
  session_id       TEXT NOT NULL,
  commit_sha       TEXT,                     -- NULL for uncommitted/working-tree changes
  file_path        TEXT NOT NULL,
  change_type      TEXT CHECK(change_type IN ('modified','added','deleted','renamed')),
  symbols_modified TEXT,                     -- JSON array: [{"name","kind","change"}]
  diff_snippet     TEXT,                     -- sanitized, truncated (see 5.1)
  timestamp        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS test_runs (        -- NEW: populated by `cb run`
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  command     TEXT NOT NULL,
  exit_code   INTEGER NOT NULL,
  passed      INTEGER, failed INTEGER, skipped INTEGER,
  failures    TEXT,                           -- JSON array of {"test","message"} (max 10, sanitized)
  timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS handoffs (
  id                     TEXT PRIMARY KEY,
  session_id             TEXT,                -- NEW: link to session
  from_agent             TEXT NOT NULL,
  to_agent               TEXT NOT NULL,
  raw_token_count        INTEGER,
  compressed_token_count INTEGER,
  method                 TEXT,                -- 'ollama' | 'groq' | 'openai' | 'deterministic'
  payload_content        TEXT NOT NULL,
  written_path           TEXT,
  created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_session ON decision_logs(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_changes_session   ON file_changes(session_id, timestamp);
```

### 4.2 Local state (`.context/state.json`)

```json
{
  "schema_version": 1,
  "project_name": "my-app",
  "active_session_id": "sess_a1b2c3d4e5f6",
  "active_branch": "feature/auth-refactor",
  "last_handoff": {
    "timestamp": "2026-10-06T21:00:00Z",
    "from": "claude",
    "to": "cursor",
    "raw_tokens": 15240,
    "compressed_tokens": 412,
    "compression_ratio": "97.3%",
    "method": "ollama"
  }
}
```
`compression_ratio = 1 - compressed/raw`, computed, never hard-coded.

### 4.3 Config (`~/.config/contextbridge/config.json`, per-project override in `.context/config.json`)

```json
{
  "llm": {
    "provider": "ollama",             
    "model": "qwen2.5-coder:7b",
    "ollama_host": "http://localhost:11434",
    "cloud_fallback": false,          
    "cloud_provider": "groq",
    "timeout_seconds": 60
  },
  "capture": {
    "max_diff_chars_per_file": 4000,
    "max_files_per_handoff": 25,
    "ignore_globs": ["*.lock", "node_modules/**", "dist/**", "*.min.js", ".context/**"]
  },
  "handoff": { "target_tokens": 400, "hard_cap_tokens": 600 },
  "vector": { "enabled": false }
}
```
API keys are read **only from environment variables** (`GROQ_API_KEY`, `OPENAI_API_KEY`), never written to config or the DB. Use `platformdirs` for the config path so Windows/macOS/Linux all work.

---

## 5. Core Requirements

### Module 1: Ingestion

**5.1 Git observer (`git_observer.py`)**
- Use `subprocess` with `git` (no GitPython dependency). Always pass `-C <repo_root>` and handle: not a repo, detached HEAD, no commits yet, merge/rebase in progress.
- Provide: current branch, status (staged/unstaged/untracked), commits since session start, per-file diff, rename detection (`-M`).
- Skip binary files and anything matching `ignore_globs`; truncate each diff to `max_diff_chars_per_file`, keeping hunk headers.
- **Hook install (`cb init`):** write a `post-commit` hook between marker lines (`# >>> contextbridge >>>` / `# <<< contextbridge <<<`). If a hook already exists, **append** the block; never overwrite. Respect `core.hooksPath`. The hook must be non-blocking: `cb hook post-commit >/dev/null 2>&1 || true`, run in the background, and exit 0 always. Provide `cb init --uninstall-hooks`.
- On branch change, start a new session (or resume the existing active session for that branch).

**5.2 AST parser (`ast_parser.py`)**
- Languages: Python, JavaScript/TypeScript (incl. TSX), Go, Rust.
- For each changed file, parse the old and new versions and **map diff hunks to symbols**: output `[{name, kind: function|class|method|export, change: added|modified|removed, signature}]`. This is the key value over raw line numbers; test it carefully.
- If parsing fails or the language is unsupported, fall back to hunk-header context (`@@ ... @@ def foo`) and then to file-level only. Never crash ingestion.

**5.3 Shell listener (`shell_listener.py` + `cb run`)**
- A passive terminal listener is fragile and invasive. Instead, `cb run -- pytest -q` executes the command, streams output unchanged, records the exit code, and parses pass/fail counts and failing test names for pytest, jest/vitest, `go test`, `cargo test`. Unknown commands store only the exit code and the last 20 lines (sanitized).
- Document an optional shell alias (`alias t='cb run -- pytest'`).

**5.4 Sanitizer (`sanitizer.py`)**
- Runs on **every** piece of text *before it touches SQLite, Chroma, a log, or any LLM prompt* (not only before vector storage).
- Patterns: AWS access keys (`AKIA...`) and secret keys, JWTs, `Bearer` tokens, GitHub/Slack/Stripe/OpenAI/Anthropic key prefixes, private key blocks (`-----BEGIN ... PRIVATE KEY-----`), `.env`-style `KEY=value` where the key name matches `(SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE)`, basic-auth URLs, and high-entropy strings (> 32 chars, Shannon entropy threshold) as a last resort.
- Replace with `[REDACTED:<type>]`. Never include the matched value in logs.
- Also skip entire files named `.env*`, `*.pem`, `*.key`, `id_rsa*`.
- Table-driven tests with positive and negative cases (avoid redacting git SHAs and UUIDs).

### Module 2: Storage

- `.context/` lives at the repo root; `cb init` appends `.context/` to `.gitignore` if missing (idempotent). Contains `state.db`, `state.json`, `vector_db/`, `config.json`.
- **Vector store (optional):** embed decision logs and diff snippets (already sanitized) using Chroma's default ONNX embedder or Ollama embeddings. When unavailable, `cb search` falls back to SQL `LIKE` search and says so.
- Commands must locate `.context/` by walking up from the cwd (like git), and fail with a clear message ("Run `cb init`") if not found.

### Module 3: Synthesis

- **Context builder:** collect the active session's decisions, file changes (with symbols), test runs, and branch/commit summary. Prioritize in this order when over budget: blockers, architecture decisions, failing tests, changed symbols, diff snippets, notes. Cap input to the model's context (default 12k tokens), truncating lowest priority first.
- **Raw token count** = tokens of the full uncompressed context the next agent would otherwise need (all selected diffs + logs), computed before truncation for the model input. Report both numbers honestly.
- **LLM compression** with this system prompt (configurable):
  > "You are a senior systems architect creating an emergency session handoff. Summarize the completed work, modified files (with key symbols), architectural decisions, failing tests, and current blockers into a hyper-dense, bulleted summary under 400 tokens. Do not invent details not present in the input."
- **Output validation:** count tokens; if over `hard_cap_tokens`, retry once with a stricter instruction, then truncate by priority. Strip any preamble the model adds.
- **Provider routing:** Ollama (default) → if unreachable and `cloud_fallback: true` and an API key is set → cloud → else **deterministic fallback**. Cloud use must print a one-line notice, and only sanitized text is ever sent.
- **Deterministic fallback:** template-based summary (sections: Goal/Branch, Done, Files & symbols, Decisions, Failing tests, Blockers, Next steps from `todo` logs). Always works offline.

### Module 4: Adapters

```python
from abc import ABC, abstractmethod
from pathlib import Path

class BaseAdapter(ABC):
    name: str

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    @abstractmethod
    def inject_payload(self, compressed_payload: str, active_files: list[str]) -> Path:
        """Write the payload to the target agent's file and return the path written."""

    @abstractmethod
    def cleanup(self) -> None:
        """Remove only the content ContextBridge injected."""
```

- **All adapters are idempotent:** re-running a handoff replaces the previous ContextBridge block rather than appending duplicates. Use markers `<!-- contextbridge:start -->` / `<!-- contextbridge:end -->`.
- **Cursor:** write `.cursor/rules/handoff.mdc` with frontmatter (`description`, `alwaysApply: true`). Create directories as needed. Cleanup deletes the file.
- **Claude Code:** upsert a `<context_handoff>...</context_handoff>` block in `CLAUDE.md` (create if missing; never touch other content). Cleanup removes only the block.
- **Generic:** write `HANDOFF.md` at repo root (overwrite only if it carries the ContextBridge marker; otherwise ask or use `--force`).
- Payload layout: header (from/to/timestamp/branch), the compressed summary, `active_files` list (top N by recent change), and a short "how to resume" line.
- Registry resolves `claude`, `cursor`, `generic` (aliases: `claude-code`, `aider`, `opencode` map to generic unless a dedicated adapter exists). Unknown agent → clear error listing valid names.

---

## 6. CLI Reference

| Command | Behavior |
|---|---|
| `cb init [--no-hooks]` | Create `.context/`, DB, state, config; update `.gitignore`; install post-commit hook; create the first session. Idempotent |
| `cb log "msg" [--tag bug\|architecture\|blocker\|note\|todo]` | Sanitize and store a note in the active session |
| `cb run -- <cmd>` | Run a command, store test results (see 5.3); returns the command's exit code |
| `cb hook post-commit` | Internal. Record the latest commit's file changes and symbols. Silent, never fails loudly |
| `cb status` | Rich dashboard: branch, session age, changed files/symbols, recent decisions, last test run, last handoff, token stats |
| `cb handoff --from <agent> --to <agent> [--dry-run] [--no-llm] [--print]` | Build context, compress, inject, record in `handoffs`, update `state.json`. `--dry-run` prints the payload without writing |
| `cb search "query"` | Semantic search (vector) or keyword fallback |
| `cb clear [--session] [--all] [--yes]` | Archive or delete session data; also `--cleanup-adapters` to remove injected files. Confirm before destructive actions |

Global: `--verbose`, `--repo PATH`. Exit codes: 0 ok, 1 user error, 2 environment error (no git, no `.context/`), 3 LLM failure (after fallback fails).

---

## 7. Quality Requirements

- **Performance:** `cb log` < 200 ms; `cb hook post-commit` < 2 s on a 1k-file repo; hook runs in the background.
- **Reliability:** every capture path catches exceptions, logs to `.context/cb.log`, and degrades gracefully.
- **Security:** sanitizer applied at all boundaries; no secrets in config/DB; cloud is opt-in; payloads never include `.env` content.
- **Testing:** unit tests per module; `test_cli_e2e.py` runs init → log → commit → run → handoff in a temp repo using the deterministic path (no network, no Ollama). LLM calls are mocked. Target 80%+ coverage on `core/` and `synthesis/`.
- **Docs:** README with install, a 5-minute quickstart, per-agent setup, privacy notes, and troubleshooting.

---

## 8. Phased Build Plan

Build in phases. **Stop at the end of each phase and report what was built, how to run it, and test output.**

### Phase 1: CLI shell and database foundations
- `pyproject.toml` with extras (`vector`, `cloud`, `dev`), console script `cb`, ruff/mypy/pytest config.
- `main.py`, `config.py`, `errors.py`, `storage/db.py` (migrations, WAL), `storage/workspace.py`, `core/sanitizer.py`.
- `cb init` (no git hook yet) and `cb log` (sanitizes before storing), `cb status` (basic version: session + decisions).
- **Acceptance:** `cb init` twice is safe; `.context/` is gitignored; `cb log "x" --tag bug` stores a sanitized row; sanitizer tests pass; `pytest` green.

### Phase 2: Git and AST ingestion
- `git_observer.py`, `ast_parser.py` (Python + TS/JS first, then Go + Rust), `hook_cmd.py`, hook install/uninstall, `shell_listener.py` + `cb run`.
- **Acceptance:** in a temp repo, a commit that edits one function records that function (not just the file) in `file_changes`; an existing `post-commit` hook is preserved; `cb run -- pytest` records pass/fail counts; binary and ignored files are skipped.

### Phase 3: Synthesis and LLM integration
- `token_counter.py`, `context_builder.py`, `llm_client.py` (Ollama; Groq/OpenAI optional), `compressor.py` (LLM + deterministic fallback), full `cb status` dashboard with token stats, optional `vector_store.py` and `cb search`.
- **Acceptance:** with no Ollama running, `cb handoff --dry-run` still prints a valid payload via the fallback; output respects `hard_cap_tokens`; mocked-LLM tests cover retry, timeout, and over-length output; raw vs compressed counts are shown.

### Phase 4: Adapters and handoff workflow
- Three adapters + registry, `cb handoff`, `cb clear`, E2E tests, README.
- **Acceptance:** handoff to each adapter writes the correct file; running twice does not duplicate the block; `cleanup` leaves user content in `CLAUDE.md` intact; `last_handoff` is updated; full E2E passes in CI.

### Roadmap (post-v1, do not build now)
1. MCP server (`cb serve`) so agents can *pull* context on demand instead of file injection.
2. Adapters for Aider (`.aider.conf` / `CONVENTIONS.md`), Windsurf, Gemini CLI, Copilot instructions.
3. Auto-handoff trigger when an agent's rate-limit message is detected.
4. Optional encrypted export/import of a session for moving between machines.

---

## 9. Definition of Done (v1)

- `uv tool install .` gives a working `cb` command on macOS, Linux and Windows (hooks via Git Bash on Windows).
- A new user can run `cb init`, work, commit, and `cb handoff --from claude --to cursor` in under 5 minutes with **no** LLM configured.
- No secret in the test corpus survives into SQLite, Chroma, prompts, or handoff files.
- All phase acceptance criteria and the E2E suite pass.

---

## 10. Starter Prompt

Paste this into your coding agent to begin:

```
Please read `contextbridge_spec.md` fully. We are building Phase 1 of ContextBridge.

Start by generating `pyproject.toml` (extras: vector, cloud, dev; console script `cb`),
the package directory structure, and then implement: config.py, errors.py,
storage/db.py (with migrations), storage/workspace.py, core/sanitizer.py, and
main.py with `cb init`, `cb log`, and a basic `cb status`, using Typer, Rich and SQLite.
Write tests for the sanitizer, db, and the CLI commands. Stop when the Phase 1
acceptance criteria are met and show me the test results. Do not start Phase 2.
```
