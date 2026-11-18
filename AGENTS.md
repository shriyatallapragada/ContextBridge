<!-- contextbridge:start -->
# ContextBridge handoff
From: codex -> To: codex | 2026-11-18T07:50:17Z | Branch: phase-5-gui

## Goal
- Local-first CLI that preserves project context across AI coding agents.

## Done
- Refined handoffs to eight prioritized files with brief purposes, GUI dependency metadata, reliable source-agent attribution and clipboard labels. Added chronological turn-based chat extraction with complete statements, completion claims, final next steps, filenames and short-input warnings.
- Unified project handoffs and chat imports with a 2000-token default, essential section budgets, source files ahead of tests, test freshness, clipboard output and optional source-agent detection. Verified 541 tests.
- Recorded commit 7d79683a — Step 2: offline change logging
- Recorded commit b4b54c37 — Checkpoint before engine refactor

## Decisions
- Reserve essential handoff sections before file details. Keep test details aggregated. Model polishing preserves factual words, paths, numbers and line order.

## Current state
- 26 test files changed, 561 passing, 0 failing, 0 skipped. Exit 0. Last run 0m ago.
- Branch phase-5-gui. 13 changed project files are uncommitted.
- Tech stack from dependency metadata — Python, typer, rich, platformdirs, pathspec, tree-sitter, tree-sitter-language-pack, tiktoken, ollama, chromadb (optional vector), openai (optional cloud), groq (optional cloud), pypdf (optional gui), PySide6-Essentials (optional gui).

## Open problems
- No open problems logged.

## Next steps
- Implement Step 4 with pluggable model providers and first-run setup explaining offline operation.
- have to make the app start easily without the user having too oanything appart fom installation

## Files to read first
- contextbridge/main.py — declared application entry point. modified, uncommitted. +2 / -0 lines
- contextbridge/synthesis/compressor.py — handoff generation. modified, uncommitted. +165 / -199 lines
- contextbridge/core/watcher.py — file change monitoring. added. +265 / -0 lines
- contextbridge/chat_bridge.py — conversation handoffs. added, uncommitted. +277 / -25 lines
- contextbridge/synthesis/facts.py — project facts and ranking. added, uncommitted. +209 / -0 lines
- contextbridge/synthesis/document.py — handoff section rendering. added, uncommitted. +92 / -0 lines
- contextbridge/storage/workspace.py — project storage and discovery. modified. +74 / -17 lines
- contextbridge/service.py — shared project operations. modified, uncommitted. +47 / -15 lines
<!-- contextbridge:end -->
