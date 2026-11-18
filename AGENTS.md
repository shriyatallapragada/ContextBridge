<!-- contextbridge:start -->
# ContextBridge handoff
From: codex -> To: codex | 2026-11-18T08:16:06Z | Branch: phase-5-gui

## Goal
- Local-first CLI that preserves project context across AI coding agents.

## Done
- Polished chat goals, neutral completion claims with contextual detail, current-state deduplication and explicit-only follow-ups. Implemented Step 4 optional providers, offline defaults, first-run setup, endpoint-scoped OS-keyring credentials and deterministic fallback. Step 4 implementation supersedes the earlier todo to implement it.
- Refined handoffs to eight prioritized files with brief purposes, GUI dependency metadata, reliable source-agent attribution and clipboard labels. Added chronological turn-based chat extraction with complete statements, completion claims, final next steps, filenames and short-input warnings.
- Unified project handoffs and chat imports with a 2000-token default, essential section budgets, source files ahead of tests, test freshness, clipboard output and optional source-agent detection. Verified 541 tests.
- Recorded commit 2d9cf797 — Step 3 follow-ups: handoff and chat-import fixes
- Recorded commit 7d79683a — Step 2: offline change logging
- Recorded commit b4b54c37 — Checkpoint before engine refactor

## Decisions
- New provider selections disable hosted fallback. Existing explicit legacy provider configurations remain readable. Model transports use direct bounded HTTP requests and Ollama num_ctx=16384 by default.
- Reserve essential handoff sections before file details. Keep test details aggregated. Model polishing preserves factual words, paths, numbers and line order.

## Current state
- 29 test files changed, 575 passing, 0 failing, 0 skipped. Exit 0. Last run 0m ago.
- Branch phase-5-gui. 13 changed project files are uncommitted.
- Tech stack from dependency metadata — Python, typer, rich, platformdirs, pathspec, tree-sitter, tree-sitter-language-pack, tiktoken, httpx, jsonschema, keyring, chromadb (optional vector), openai (optional cloud), groq (optional cloud), pypdf (optional gui), PySide6-Essentials (optional gui).

## Open problems
- No open problems logged.

## Next steps
- Step 4 is complete. Review its results before starting Step 5. The previous Step 4 implementation todo is superseded.
- Implement Step 4 with pluggable model providers and first-run setup explaining offline operation.
- have to make the app start easily without the user having too oanything appart fom installation

## Files to read first
- contextbridge/main.py — declared application entry point. modified, uncommitted. +2 / -0 lines
- contextbridge/core/watcher.py — file change monitoring. added. +265 / -0 lines
- contextbridge/synthesis/facts.py — project facts and ranking. added. +209 / -0 lines
- contextbridge/synthesis/llm_client.py — llm client module (name-based description). modified, uncommitted. +39 / -161 lines
- contextbridge/providers/http.py — http module (name-based description). added, uncommitted. +171 / -0 lines
- contextbridge/providers/settings.py — settings module (name-based description). added, uncommitted. +124 / -0 lines
- contextbridge/gui/provider_form.py — provider form module (name-based description). added, uncommitted. +152 / -0 lines
- contextbridge/synthesis/document.py — handoff section rendering. added. +92 / -0 lines
<!-- contextbridge:end -->
