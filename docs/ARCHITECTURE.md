# How ContextBridge works

## 1. What it is and why it exists

When you switch AI coding agents mid-task (Claude Code to Cursor, say, or because of a rate limit), the new agent knows nothing. It doesn't know what you decided, what's broken, which functions you changed, or whether tests pass. ContextBridge records that quietly while you work. It compresses it into a short, secret-free summary of at most 600 tokens by default. It then writes that summary into the file the next agent reads automatically.

There's one design rule behind everything: **it must work with no network, no LLM and no setup**, and nothing sensitive may leave your machine unless you opt in.

The pipeline has four stages:

```
 CAPTURE                  STORE               SYNTHESIZE                 INJECT
 -------                  -----               ----------                 ------
 cb log          -+
 git post-commit -+--->  .context/state.db --> context builder --> compressor --> adapter --> CLAUDE.md
 cb run          -+     (SQLite, sanitized)   (priority-budgeted)  (LLM or        (marker      .cursor/rules/handoff.mdc
 + live git state                                                  template)       blocks)      HANDOFF.md
```

The whole thing is about 5,400 lines of Python in `contextbridge/`, covered by 353 tests.

---

## 2. Code layout

| Package | Files | Role |
|---|---|---|
| `main.py`, `cli/` | `init_cmd`, `log_cmd`, `run_cmd`, `hook_cmd`, `status_cmd`, `handoff_cmd`, `search_cmd`, `clear_cmd`, `common` | The Typer commands. Thin: they parse options and call into the layers below |
| `core/` | `git_observer`, `ast_parser`, `shell_listener`, `sanitizer` | Reading the outside world (git, source code, command output) and scrubbing secrets |
| `storage/` | `db`, `workspace`, `vector_store` | SQLite schema, the `.context/` folder, `state.json`, session logic, optional Chroma index |
| `synthesis/` | `token_counter`, `context_builder`, `llm_client`, `compressor` | Turning stored data into a budgeted handoff payload |
| `adapters/` | `base_adapter`, `claude_adapter`, `cursor_adapter`, `generic_adapter`, `registry` | Writing the payload into agent files, and removing it again |
| `config.py`, `errors.py` | | Layered configuration, and exceptions that carry exit codes |

**Dependencies run one way**: `cli` -> `synthesis`/`adapters` -> `storage`/`core` -> `sanitizer`. The one slight exception is that `git_observer` imports the marker constants from `adapters`, so it can ignore injected content.

---

## 3. The workspace (`.context/`)

`cb init` creates `<project>/.context/` containing:

| File | Purpose |
|---|---|
| `state.db` | The SQLite database. All captured data |
| `state.json` | Small pointer file: project name, active session, active branch, `last_handoff` |
| `config.json` | Per-project config overrides (starts as `{}`) |
| `cb.log` | Log file. Every line is sanitized by a custom formatter |
| `vector_db/` | Chroma index, created only if you enable semantic search |

**Finding the workspace.** Commands walk up from the current directory looking for `.context/state.db`, as git does. Running `cb log` from a subdirectory works, and `--repo PATH` overrides the start point. If nothing is found, you get `NotInitializedError` with the hint "Run `cb init`" and exit code 2. `cb init` itself anchors at the nearest ancestor containing `.git` (or the current directory if there isn't one).

**`.gitignore`.** `cb init` appends `.context/` if it's not already there. It recognises equivalents (`.context`, `/.context/`, and so on), so it's idempotent.

**`state.json`** is written atomically (temp file plus `os.replace`) and preserves unknown keys. It looks like this:

```json
{ "schema_version": 1, "project_name": "shop",
  "active_session_id": "sess_a1b2c3d4e5f6", "active_branch": "main",
  "last_handoff": { "timestamp": "...", "from": "claude", "to": "cursor",
                    "raw_tokens": 1240, "compressed_tokens": 412,
                    "compression_ratio": "66.8%", "method": "deterministic" } }
```

### Configuration layering (later wins)

1. Built-in defaults
2. Global file: `~/.config/contextbridge/config.json` (via `platformdirs`; `CB_CONFIG_DIR` overrides)
3. `.context/config.json`
4. Environment: `CB_LLM_PROVIDER`, `CB_LLM_MODEL`, `CB_OLLAMA_HOST` / `OLLAMA_HOST`, `CB_CLOUD_FALLBACK`

Values are type-checked, unknown keys are ignored (forward compatibility), and `validate()` enforces sane relationships. For example `target_tokens <= hard_cap_tokens <= max_input_tokens`. **API keys are never in config.** `GROQ_API_KEY` and `OPENAI_API_KEY` are read from the environment only, and `save_config` strips any key whose name looks like a secret.

Key defaults:

| Setting | Default |
|---|---|
| `llm.model` | `qwen2.5-coder:7b` |
| `llm.timeout_seconds` | 60 |
| `capture.max_diff_chars_per_file` | 4000 |
| `capture.max_files_per_handoff` | 25 |
| `capture.ignore_globs` | `*.lock`, `node_modules/**`, `dist/**`, `*.min.js`, `.context/**` |
| `handoff.target_tokens` | 400 |
| `handoff.hard_cap_tokens` | 600 |
| `handoff.max_input_tokens` | 12000 |

---

## 4. The database

`storage/db.py` is a thin wrapper over stdlib `sqlite3`: WAL mode, `foreign_keys=ON`, and a 5-second busy timeout. The connection runs in autocommit, and every write uses an explicit `BEGIN IMMEDIATE` transaction.

**Versioning.** The schema version lives in `PRAGMA user_version`. Migrations are a tuple of SQL scripts, applied in order, each in its own transaction. A database newer than the code is rejected with an "Upgrade ContextBridge" message. There are two so far: v1 (sessions, decisions, file changes, handoffs) and v2 (`test_runs`, plus two indexes).

**IDs and times.** IDs are `prefix_<12 hex>`: `sess_`, `dec_`, `chg_`, `run_`, `hand_`. Timestamps are UTC ISO-8601 (`2026-10-07T15:51:44Z`).

| Table | What a row is | Notable details |
|---|---|---|
| `sessions` | One stretch of work on one branch | `status`: `active`/`archived`/`handed_off` (CHECK). `handed_off` is currently unused |
| `decision_logs` | A `cb log` entry | `tag` in note/bug/architecture/blocker/todo (CHECK). Cascades on session delete |
| `file_changes` | One file in one **commit** | `symbols_modified` is a JSON array; `diff_snippet` is sanitized and truncated. A **unique partial index on `(commit_sha, file_path)`** makes re-recording a commit a no-op |
| `test_runs` | One `cb run` | `passed`/`failed`/`skipped` can be NULL (unknown). `failures` is JSON, max 10 |
| `handoffs` | One real `cb handoff` | Raw and compressed token counts, method, full payload, written path. **Its foreign key has no cascade**, so clearing sessions deletes handoffs first |

Uncommitted changes are never stored. They're recomputed live from git each time (section 8).

**Last line of defence.** Every `add_*` method runs its text through the sanitizer again, even if the caller already did. Callers can forget; the storage layer can't.

---

## 5. Sessions

A session ties together everything done on one branch. `Workspace.resolve_active_session` decides which one is current:

1. Take the session ID from `state.json`.
2. Keep it if it's still `active` and its branch matches the current branch.
3. Otherwise (branch changed, or it was archived), look for an active session for the current branch, and create one if none exists.
4. **A detached HEAD keeps the current session** rather than inventing a pseudo-branch session.
5. The find-or-create runs inside a write transaction. This matters because background hooks can overlap, and without the lock two hooks right after a branch switch would each create a session. A regression test fails 3/3 times without the lock.

Switching branches and committing therefore moves you between sessions automatically, and switching back resumes the old one. `cb clear --session` archives the current one, and the next command starts fresh.

---

## 6. Capture path A: `cb log`

`cb log "msg" --tag blocker` rejects empty messages, sanitizes the text, resolves the session, inserts the row, and prints how many secrets were redacted (counts and kinds only, never values). It's fast because it imports nothing heavy.

---

## 7. Capture path B: the git hook

### Installing it (`cb init`)

`GitObserver.install_hook()` finds the hooks directory with `git rev-parse --git-path hooks`. That honours `core.hooksPath` and worktrees. It then:

- **Creates** an executable `#!/bin/sh` file if none exists.
- **Appends** to an existing hook. The block goes before a trailing top-level `exit`, so it still runs. It sits between `# >>> contextbridge >>>` and `# <<< contextbridge <<<`. Your hook is never overwritten.
- **Refuses** hooks whose shebang isn't sh/bash/zsh/dash/ksh, and says what line to add by hand.
- **Leaves untouched** a non-executable hook. Git ignores those, so making one executable would re-enable code you'd turned off.
- Is **idempotent**. A re-run refreshes an outdated block in place without changing permissions.

`cb init --uninstall-hooks` removes only the block, and deletes the file only if nothing but the shebang remains.

The block is a backgrounded subshell with output discarded. It tries the Python interpreter that ran `cb init` first (`python -m contextbridge.main hook post-commit`), and falls back to `cb` on PATH. The reason for that order: an unrelated or older `cb` earlier on PATH can't take over the hook. The hook can't slow or fail your commit.

### What `cb hook post-commit` does

It's silent and always exits 0. Any failure goes to `cb.log`, never the terminal.

```
locate .context/   (none? exit silently)
repo_state()       (no commits yet, or rebase in progress? skip)
session = active session (may start a new one on a branch change)
pending = [HEAD] + commits since the last recorded one   (capped at 20)
for each pending commit, oldest first:
    skip if already recorded
    diff against first parent (the empty tree for a root commit)
    for each changed file: read old and new versions, map the diff to symbols
    INSERT OR IGNORE into file_changes
```

**The backfill** matters because a rebase replays commits without running the hook for each one, so the next hook run picks up anything missed. It deliberately **doesn't use timestamps** for this. Git's one-second resolution would otherwise pull in a commit made just before `cb init`. Merge commits record the first-parent diff, meaning what the merge brought in.

### The git observer (`core/git_observer.py`)

Every call is `git -C <repo> -c core.quotePath=false ...` through `subprocess`. There's no GitPython. The environment is scrubbed: `GIT_DIR` and friends are removed (git sets them inside hooks and they'd override `-C`), `LC_ALL=C` makes output parseable, `GIT_OPTIONAL_LOCKS=0` stops it contending with your own git commands, and prompts are disabled. There's a 15-second timeout.

| Capability | How |
|---|---|
| Repo state | Branch via `symbolic-ref` (None when detached), HEAD sha (None with no commits), and in-progress merge/rebase/cherry-pick/revert/bisect, found by checking marker files in the absolute git dir |
| Status | `status --porcelain=v1 -z`, which handles spaces and renames |
| Diffs | `diff -M --name-status -z`, then one patch per file. Rename detection is built in |
| Commits | `log` with `--since` plus an exact Python-side time filter, and `exclude` for "after the last recorded commit" |
| File contents | `git show rev:path`, or reading the working tree. Capped at 1 MB |

**What gets skipped, in order:** secret-looking files (`.env*`, `*.pem`, `*.key`, `id_rsa*`), your `ignore_globs`, ContextBridge's own output files, anything beyond 200 files, and binary files (detected from the patch). `dir/**` patterns match at any depth, so `node_modules/**` also catches `web/node_modules/x.js`.

**Truncation** (`truncate_diff`) keeps the file header and **every `@@` hunk header**, then fills hunk bodies in order until the character budget runs out, and appends `[... truncated N line(s) ...]`. An agent can therefore see every place that changed even when the bodies are cut.

---

## 8. Capture path C: finding *which function* changed

This is the part that makes the context useful. Line numbers mean nothing to a new agent, but "`Cart.total` was modified" does.

### Parsing (`core/ast_parser.py`)

tree-sitter does the parsing, through `tree-sitter-language-pack`. The dependency is pinned to the 0.13 series because it **bundles** the grammars; the 1.x line downloads them on first use, which doesn't suit a local-first tool or a hook that must finish in 2 seconds.

Languages are chosen by extension: Python, JavaScript (`.js .mjs .cjs .jsx`), TypeScript, TSX, Go, Rust.

Each language has its own symbol extractor producing `{name, kind, start_line, end_line, signature}`:

- **Python:** functions, classes, methods. Decorators are included in the symbol's range, so changing `@property` counts as a change to that method.
- **JS/TS:** function declarations, classes, interfaces and enums (kind `class`), methods, and `const f = () => ...` (kind `function`). Arrow-function class fields count as methods. Exported type aliases are kind `export`. `export default` becomes the name `default`. An `export` statement's range is used, so editing the `export` keyword counts.
- **Go:** functions, plus methods qualified by receiver type (`Server.Start`, with `*` and generics stripped), and structs/interfaces as classes.
- **Rust:** functions, structs/enums/traits as classes, and methods inside `impl Type` as `Type::method`, with `mod` nesting as `mod::fn`.

The signature is the declaration text up to the body, collapsed to one line and capped at 160 characters.

### Mapping a diff to symbols

1. Parse the diff into hunks, tracking exact changed line numbers: **new-file numbers for added lines, old-file numbers for removed lines**.
2. Parse both the old and the new version of the file.
3. Each changed line goes to the **innermost** symbol containing it. Editing a method blames the method, not the class.
4. Symbols are matched old-to-new by name **plus occurrence order** (`total`, `total#2`). This is what handles Python property setters, JS `get`/`set` pairs and overloads, which share a name. (A code review caught this being wrong in the first version.)
5. Classification:
   - **added**: in new only (a new class is reported once, not once per new method)
   - **removed**: in old only
   - **modified**: touched by a hunk **and** the symbol's text really changed. Unchanged functions that merely shifted down because lines were inserted above them aren't reported.
6. Added or deleted files list their top-level symbols.

### The fallback chain (never raises)

symbols from the syntax tree -> if parsing fails or a source can't be read, the `@@ ... @@ def foo` text git puts in hunk headers -> file-level only (an empty list). It uses git's change type rather than guessing "added" from a missing source. A file too large to read therefore isn't misreported as brand new.

---

## 9. Capture path D: `cb run`

`cb run -- pytest -q` runs your command, then records the result.

**Running.** The child's stdout and stderr go through two pipes. Two threads copy the bytes **unchanged** to your terminal, so colours and progress bars survive. They also keep decoded, ANSI-stripped lines (the last 5000) for parsing. If your stdout is a terminal, it sets `FORCE_COLOR`, `PY_COLORS` and `CARGO_TERM_COLOR` so tools keep their colours despite the pipe. Ctrl-C reaches the child (same process group) and the wrapper waits for it. Exit codes follow shell conventions: 127 for command not found, 126 for permission denied, 128+n for a signal.

**Parsing.** The runner is guessed from argv (`uv run pytest`, `python -m pytest`, `npx vitest`, `go test`, `cargo test`). If that fails, the output is sniffed against each parser in turn. That's why `make test` or `npm test` still work.

| Runner | Reads |
|---|---|
| pytest | The summary line (`1 failed, 3 passed, 1 skipped in 0.05s`); `FAILED path - message` lines |
| jest | `Tests:` line; failure bullets plus the next lines |
| vitest | `Tests  1 failed \| 7 passed (9)`; `FAIL file > suite > test` |
| go test | `--- PASS/FAIL/SKIP` lines. **Without `-v`, the pass count is stored as unknown, not invented** |
| cargo test | `test result:` lines **summed across test binaries**; panic messages from `---- name stdout ----` |

Anything else stores the exit code plus the last 20 lines. At most 10 failures are kept, with messages capped at 300 characters.

**Failure isolation.** Recording happens after the command finishes, and a recording failure can never change the command's exit code. Outside an initialized repo, `cb run` still runs your command and says "result not recorded".

---

## 10. The sanitizer (`core/sanitizer.py`)

Everything that touches storage, logs, prompts or agent files passes through `sanitize()`. Matches become `[REDACTED:<type>]`.

Rules run most-specific first, so an Anthropic key is labelled `anthropic_key` rather than `openai_key`:

1. Private key blocks (`-----BEGIN ... PRIVATE KEY-----` through the end marker)
2. Credentials in URLs (`scheme://user:pass@`, which keeps the scheme)
3. JWTs, `Bearer` tokens
4. Anthropic, Stripe, OpenAI, GitHub, Slack keys and webhooks
5. AWS access keys, and AWS secret keys by name
6. `.env`-style assignments whose upper-case name contains SECRET, TOKEN, PASSWORD, API_KEY or PRIVATE. This keeps the variable name and drops the value. Values that look like code, containing brackets, are left alone.
7. **Last resort: high-entropy strings.** Candidates are 33+ characters, with Shannon entropy >= 4.5. Hex strings, UUIDs, git SHAs, path-like and identifier-like strings, and anything with many `_`/`-` are excluded, so SHAs survive.

`sanitize_with_report` returns counts per type and never the matched values. `is_sensitive_file` decides which whole files to skip.

It's pattern-based. It lowers the risk but can't guarantee a secret is caught, and the README says so.

---

## 11. Synthesis

### Token counting (`synthesis/token_counter.py`)

tiktoken's `cl100k_base` encoding, labelled "approx." everywhere because other models count differently. tiktoken **downloads its encoding on first use with no timeout**, into a temp directory the OS may clear. So:

- The encoding goes in a persistent cache (`~/Library/Caches/contextbridge/tiktoken` on macOS).
- Loading runs in a background thread with a **5-second deadline**. On timeout, counts become `ceil(chars / 4)`, so offline use never hangs. `CB_TOKENIZER=estimate` forces this.

### The context builder (`synthesis/context_builder.py`)

It assembles a `SessionContext`:

- Decisions from the database.
- **File changes merged per file.** If `a.py` appears in three commits, you get one entry. Symbols are folded oldest to newest: added-then-removed disappears, and added-then-modified stays "added" (new this session).
- Commits this session: the ones the hook recorded, plus anything newer.
- The last test run with its failures.
- **Live changes that aren't in the database:** uncommitted edits (diff against HEAD, with symbols) and **untracked files** (synthesized as added files). Real handoffs happen mid-work, and brand-new files are often the most important thing. These are sanitized on the way in, and `.env` and binary files are skipped.
- Capped at `max_files_per_handoff` (25), with "... and N more file(s)".

Everything becomes a `ContextItem` with a priority. **Lower numbers survive longest:**

| Priority | Content |
|---|---|
| 0 | Session header (project, branch, commits) |
| 1 | Blockers |
| 2 | Architecture decisions |
| 3 | Failing tests, and `bug` logs |
| 4 | Changed files and symbols |
| 5 | Todos |
| 6 | Diff snippets |
| 7 | Notes |

The spec didn't place `bug` and `todo`, so those two slots are a design choice.

**Two token numbers, deliberately separate.** `raw_tokens` is the size of the *full* context before any truncation: what the next agent would otherwise need. The model's input is then capped at `max_input_tokens` (12000) by `fit_items`. That function keeps whole items in priority order, truncates the first one that doesn't fit (if more than 40 tokens remain), and drops the rest. Compression ratio is always computed against the true raw size.

### The LLM client (`synthesis/llm_client.py`)

Routing:

```
provider = ollama      -> Ollama, then (if cloud_fallback and the key is set) Groq/OpenAI
provider = groq|openai -> that provider (if its key is set)
provider = none, or --no-llm -> no clients
```

Ollama errors map to three kinds: **unavailable** (connection refused, model missing with an `ollama pull` hint), **timeout**, and **other**. Cloud clients use `max_retries=0` and your timeout, and `llm.cloud_model` overrides the default per-provider model, since the Ollama model name isn't valid for Groq or OpenAI. All calls use temperature 0.2.

### The compressor (`synthesis/compressor.py`)

For each candidate client, in order:

1. **Notice.** If it's a cloud client, print a one-line notice saying sanitized context is being sent, and why.
2. **Re-sanitize** the prompt text just before it leaves the process, as defense in depth.
3. **Ask** using your configured system prompt (the spec's default: "senior systems architect creating an emergency session handoff...") plus the context.
4. **Clean the output** by stripping `<think>...</think>`, wrapping code fences, and chatty openers ("Here is the summary:").
5. **Validate length.** Within budget: accept. Over: **retry once** with a stricter instruction (75% of target), then **truncate line by line** with a note if still over.
6. Any provider error moves to the next candidate. Even an unexpected exception from an SDK is caught.

If nothing works, the **deterministic fallback** runs. It can't fail and needs nothing external. It fills a fixed template (Goal/Branch, Done, Files & symbols, Decisions, Failing tests, Blockers, Next steps). When over budget it repeatedly deletes the lowest-priority, latest-in-document line until it fits.

### The payload

```
# ContextBridge handoff
From: claude -> To: cursor | 2026-10-07T15:51:44Z | Branch: main

<summary: from the LLM, or the template>

## Active files
- cart.py
- ...                      (top 10, most recently changed)

To resume: read the active files, check `cb status` for live state, and continue from the next steps above.
```

The **whole payload** is held to `hard_cap_tokens`, not just the summary. The header and footer are measured first, the summary gets the remainder (minimum 80), and a final check trims if token boundaries shift when joined.

---

## 12. Adapters: writing into agent files

All adapters share one idea: ContextBridge owns **only the text between two marker comments**, and everything else in the file is yours.

```
<!-- contextbridge:start -->
...payload...
<!-- contextbridge:end -->
```

| Adapter (`--to`) | Writes | Details |
|---|---|---|
| `claude`, alias `claude-code` | `CLAUDE.md` | Wraps the payload in `<context_handoff>...</context_handoff>` inside the markers and appends it after your content. Cleanup deletes the file only if it's left empty, meaning ContextBridge created it |
| `cursor` | `.cursor/rules/handoff.mdc` | YAML frontmatter (`description`, `alwaysApply: true`) **outside** the markers, since Cursor requires it first in the file. Cleanup deletes the file and then any now-empty `.cursor/rules` and `.cursor` directories |
| `generic`, aliases `aider`, `opencode` | `HANDOFF.md` | The marker block is the whole file |

### Guarantees (all tested)

- **Idempotent.** Re-running replaces the existing block in place, keeping its position. If duplicates exist they collapse to one.
- **Byte-exact cleanup.** The append adds one blank-line separator and the removal takes exactly that back out, so `CLAUDE.md` returns to its original bytes. It also works for CRLF files (detected, kept), files with no trailing newline (only a trailing `\n` is added), and files containing non-UTF-8 bytes (read and written byte-preservingly).
- **Atomic writes.** Written to a temp file in the same directory, then `os.replace`d, with file permissions copied. A failed write leaves the original and no stray temp file.
- **Can't be forged.** If a decision you logged contains the literal marker text or `</context_handoff>`, it's escaped before writing. Otherwise a note could corrupt `CLAUDE.md`.
- **Won't guess.** Unbalanced markers (a start with no end, from hand-editing) abort with an error and leave the file untouched.
- **Won't clobber.** `HANDOFF.md` and `handoff.mdc` are overwritten only if they carry the marker. Otherwise you get an error with a hint to use `--force`.
- **Sanitized again** before writing.

### The loop guard

Without this, the system would eat its own output. After a handoff, `CLAUDE.md` shows as a modified file in git, containing the previous payload. The next handoff would summarize that diff, so payloads would snowball. The git observer prevents this three ways:

1. For any `CLAUDE.md` it recomputes the diff after stripping the injected block from both the old and new versions. A real edit of yours is still reported.
2. If the block was the only change, the file is skipped.
3. `HANDOFF.md` and `.cursor/rules/handoff.mdc` are always skipped. The cost is that a hand-written `HANDOFF.md` is never reported as a change.

A test runs three handoffs and asserts the payload size stays within 5 tokens.

---

## 13. The commands in detail

### `cb handoff --from A --to B`

```
1. require --from and --to (unless --dry-run)
2. open workspace + config
3. resolve the --to adapter  -> a typo like "gemini" fails here, before any work
4. resolve session, build SessionContext (DB + live git state)
5. build_handoff(): compress within the cap, with the notices
6. --dry-run?  show payload + stats + "would write X (not written)"; stop
7. adapter.inject_payload(payload, active_files)         <- the file is written
8. INSERT into handoffs; set state.json last_handoff      <- bookkeeping
9. print "Handed off A -> B: wrote <file>" + stats table
```

The file is written **before** the bookkeeping, so a failed write isn't recorded as a handoff. `--from` is a free label (you may be leaving an agent with no adapter), and known names and aliases are normalised, so `Claude-Code` becomes `claude`. `--print` sends the raw payload to stdout and everything else to stderr, so you can pipe it. The stats show method (and *why* it fell back, e.g. "ollama: not reachable"), raw tokens, payload tokens against the cap, and the ratio. A negative ratio, which happens on tiny sessions, gets a plain-language note.

### `cb status`

It uses the same context builder, so status shows exactly what a handoff would see. The panel covers branch, session age, decision counts by tag, commits, changed-file count with how many are uncommitted, last test run, last handoff, and raw context size. Below it: a changed-files table with symbols (uncommitted marked `*`), failing tests if the last run failed, and recent decisions.

### `cb search`

Keyword mode (the default) uses SQL `LIKE` over decision text, file paths, symbol JSON and diff snippets, newest first, with `%` and `_` escaped so searching for `100%` works. Semantic mode (`vector.enabled` plus the `vector` extra) uses Chroma. Indexing is **lazy**: it happens at search time, adding only rows not yet embedded and removing rows deleted from SQLite. This keeps `cb log` and the hook from ever importing Chroma. Scores are `1 - cosine distance`. If Chroma is unavailable it falls back to keyword search and tells you.

### `cb clear`

- `--session` (the default with no flags): archives the active session. Nothing is deleted, and the next command starts fresh.
- `--all`: shows exact counts of what will be deleted, asks for confirmation unless `--yes`, then deletes handoffs, then sessions (which cascade), resets state, and removes `vector_db`. Config and agent files survive. Without a terminal to answer the prompt it refuses rather than deleting.
- `--cleanup-adapters`: runs each adapter's cleanup. It can be combined with the others.
- `--session` and `--all` together is an error.

---

## 14. Error handling and logging

**Exit codes**, carried by the exception types: `0` ok, `1` user error (bad input, unknown agent), `2` environment error (no git, not initialized, corrupt config or database), `3` LLM failure. Code 3 exists but is currently unreachable, because the deterministic fallback can't fail.

`error_boundary()` turns an expected error into a red message, a dim hint line, and the right exit code. Unexpected errors in *capture* paths (the hook, `cb run` recording) are caught, logged to `cb.log`, and never surfaced. `--verbose` raises the log level to DEBUG.

---

## 15. Tests and CI

**353 tests, about 40 seconds.** Overall coverage is 96%; `core/`, `synthesis/` and `adapters/` combined are also 96%.

- **Isolation (autouse fixture):** a throwaway config directory, API keys cleared, and Ollama pointed at a closed port. Tests can't reach a real LLM or touch your real config.
- **Real git:** tests build temporary repos with real commits, renames, merges, detached HEADs and hooks. A few run an actual `git commit` and wait for the background hook.
- **Mocked LLMs:** a scripted fake client covers retry, timeout, over-length output, unreachable Ollama, the cloud notice, and exceptions.
- **End to end:** `test_cli_e2e.py` runs init -> log -> commit -> run -> handoff as **real subprocesses**.
- **Regression tests** for every review finding, each confirmed to fail without its fix.

CI (`.github/workflows/ci.yml`) runs ruff, a format check, strict mypy and pytest on Ubuntu and macOS with Python 3.11 and 3.12.

---

## 16. A worked example

```
$ cb init
  .context/ created; .context/ added to .gitignore; schema v2;
  session sess_ba75... on main; hook appended to your existing post-commit

$ cb log "Prices are integer cents" --tag architecture        -> dec_... (sanitized)
$ cb log "Empty cart crashes checkout" --tag blocker

# you edit Cart.total, then:
$ git commit -m "price-aware total"
    '- hook (background): HEAD not recorded -> diff vs parent
       -> cart.py changed; old/new parsed; hunk lines fall inside Cart.total
       -> file_changes: {file: cart.py, symbols:[{Cart.total, method, modified,
                                                 "def total(self)"}]}

$ cb run -- pytest -q            -> output streams normally; stores 1 passed, 1 failed,
                                    failure "tests/test_cart.py::test_empty_cart"

# you create discount.py (untracked, never committed)

$ cb handoff --from cursor --to claude
    context: 2 decisions, 2 commits, cart.py (Cart.total), discount.py (uncommitted,
             apply_discount added), last test run FAILED -> 381 raw tokens
    routing: Ollama not reachable -> deterministic template -> 237 tokens (cap 600)
    CLAUDE.md: your rules kept, block appended
    DB: handoffs row; state.json last_handoff updated

$ cb handoff --from cursor --to claude     -> block replaced in place, still one block
$ cb clear --cleanup-adapters              -> CLAUDE.md is byte-identical to before
```

---

## 17. Known limitations

- **No real LLM was ever called during development.** Ollama and the cloud providers are verified only against mocks, and Chroma's real embedding model was never exercised (tests use a fake embedder). These paths are the least proven.
- **Untested platforms.** Python 3.11 was not available locally and Windows is not covered by CI. The CI workflow had not yet run when this document was written.
- **Sanitizing is heuristic.** A secret in an unusual format, shorter than the entropy threshold, could slip through.
- **Symbol mapping is syntactic.** It doesn't resolve references, so a changed constant that affects ten functions registers only where it was edited. Languages outside the six get only the hunk-header fallback, which is approximate.
- **Per-commit attribution.** Merge commits record the first-parent diff, and commits made during a rebase are picked up late, on the next commit.
- **`handed_off` is defined but unused.** Sessions stay active after a handoff, on purpose.
- **Roadmap features aren't built**: the MCP server, more agent adapters, auto-trigger on rate limits, and encrypted export.

---

## 18. The desktop app

A PySide6 (Qt) app that does everything the command line does, for people who'd rather click.
It lives in `contextbridge/gui/` and is deliberately thin.

- **`service.py`** is the one place operations live: set up a project, read a dashboard
  snapshot, add a note, run tests, prepare and commit a handoff, clear data. It returns plain
  data, never prints, and never imports Qt or Typer. The CLI commands call it too, so the two
  front ends can't drift apart. A handoff is **prepared** (built, nothing written) and then
  **committed** (written and recorded), so what you preview is exactly what is written.
- **Screens** (`gui/pages/`): Home, Notes, Changes, Hand off, Settings, plus a Welcome screen.
  Each is built from the small widgets in `widgets.py`. User-facing wording ("notes",
  "words", "functions") lives in `wording.py`, so the technical terms never leak.
- **Never freezes**: git, tests and AI calls run on a thread pool (`workers.py`). Results come
  back through an object that lives on the interface thread, so callbacks always run there.
  Stale refreshes are ignored by a ticket counter.
- **Look**: `theme.py` holds the four swatch colours, every derived tint, and the stylesheet.
  A test checks that every text and background pair meets WCAG AA contrast (4.5:1).
- **Artwork**: `assets.py` draws placeholder illustrations, icons and the app icon, and uses
  a designer's `<name>.svg` from `gui/assets/` instead whenever one exists (`docs/GUI_ASSETS.md`).
- **Packaging**: `packaging/ContextBridge.spec` and `scripts/build_app.py` produce a standalone
  app. In a packaged app `sys.executable` is the app, not Python, so the git hook runs it as
  `<app> --cli hook post-commit`. That mode behaves like `cb` and never opens a window
  (`docs/PACKAGING.md`).
- **Choices**: AI is "Smart" (use Ollama if running, else a template) or "Template only".
  Cloud providers stay command-line only, so no API key is ever typed into or stored by the app.
