<!-- contextbridge:start -->
# ContextBridge handoff
From: codex -> To: codex | 2026-11-18T13:37:00Z | Branch: phase-5-gui

## Goal
- Local-first CLI that preserves project context across AI coding agents.

## Done
- Fixed explainer quality for Next.js and Flutter. Root is Project root; unexplained cards show Not explained yet with inline Explain. Component descriptions now use validated, cached map-reduce over file explanations only. Explain project/component expose shared request and reserved-token budgets, importance order, pacing, rate-limit stop messages and resume for file and reduction caches without the regular eight-file cap. Added Next URL labels, literal Express/Flask route labels and TS/JS tsconfig/jsconfig aliases plus index resolution. Verified 686 passing tests, one optional ChromaDB skip, Ruff, mypy and GUI self-test. Tests cover a twelve-source-file Next fixture, invalid folder names, budgets and resume.
- Reworked Step 6 into a Flutter explainer prototype.
- Completed Step 6 by extending project_map.py and the existing diagram page with stable graph JSON, bounded overview/file/request-flow views, current SQLite purposes and labeled AI explanations, click-through dependencies, and validated deterministic Mermaid export.
- Fixed Step 5 review issues: reasoning-aware final-content parsing and higher probe budgets; persisted header-based rate pacing, cooldowns and chunk plans; opt-in handoff polishing; separately labeled and validated AI-written explanations; folder inventories and consistent progress counts.
- Implemented Step 5 offline project summaries from source, manifests, README, symbols and git history.
- Polished chat goals, neutral completion claims with contextual detail, current-state deduplication and explicit-only follow-ups.
- Refined handoffs to eight prioritized files with brief purposes, GUI dependency metadata, reliable source-agent attribution and clipboard labels.
- Unified project handoffs and chat imports with a 2000-token default, essential section budgets, source files ahead of tests, test freshness, clipboard output and optional source-agent detection.
- Recorded commit c57aeac5 — Step 4 fixes: provider errors and model listing
- Recorded commit d1127f2f — Step 4: provider layer
- Recorded commit 2d9cf797 — Step 3 follow-ups: handoff and chat-import fixes

## Decisions
- Component summaries are separate derived SQLite records keyed by member content hashes and file-explanation inputs. Folder prompts contain file explanations, not source or folder-name guesses; the same code-name validation applies. Imports resolve in Python, Dart and JS/TS; Next routes use file conventions and Express/Flask detection handles literal routes only.
- The explainer overview groups source folders into at most eight components, with cached descriptions and AI provenance. Overview arrows use only detected imports. Dart package names and startup files come from pubspec.yaml and lib/main.dart; missing import support and startup files are explained. File explanations are generated only on demand after privacy confirmation and reuse the single-file hash/provider cache.
- Graph JSON is the shared boundary for the desktop diagrams and local API. Static imports/calls and current hash-matched SQLite summaries supply labels; graph and API reads never call a model or read credentials. The API starts explicitly on 127.0.0.1 with a fresh bearer token required for every request.
- Handoff polishing defaults off and requires --polish or handoff.polish=true. Groq GPT-OSS probes use low reasoning effort with 1024 completion tokens. Summary analysis persists rate headers/cooldowns and bounded chunk plans; optional --explain text is stored separately with AI-written provenance and per-file identifier validation.
- Project summary model purposes are extractive selections from supplied documentation or static descriptions; symbols/imports must match evidence. Analyze at most eight ranked source files with twelve generation attempts per run. Handoffs consume offline/cached summaries; model analysis requires explicit cb summarize.
- New provider selections disable hosted fallback. Existing explicit legacy provider configurations remain readable. Model transports use direct bounded HTTP requests and Ollama num_ctx=16384 by default.
- Reserve essential handoff sections before file details. Keep test details aggregated. Model polishing preserves factual words, paths, numbers and line order.

## Current state
- 58 test files changed, 686 passing, 0 failing, 1 skipped. Exit 0. Last run 0m ago.
- Branch phase-5-gui. 15 changed project files are uncommitted.
- Tech stack from dependency metadata — Python, typer, rich, platformdirs, pathspec, tree-sitter, tree-sitter-language-pack, tiktoken, httpx, jsonschema, keyring, fastapi (optional api), uvicorn (optional api), chromadb (optional vector), openai (optional cloud), groq (optional cloud), pypdf (optional gui), PySide6-Essentials (optional gui).

## Open problems
- No open problems logged.

## Next steps
- Review the Next.js and Flutter explainer quality fixes and Explain project flow before expanding prototype scope. API, extra views and Mermaid polish remain deferred.
- have to make the app start easily without the user having too oanything appart fom installation

## Files to read first
- contextbridge/main.py — Typer application and `cb` console-script entry point. modified, uncommitted. +7 / -0 lines
- contextbridge/gui/app.py — Entry point for the desktop app (and for the packaged app's `--cli` mode).
- contextbridge/synthesis/project_summary.py — Bounded, privacy-filtered project inventory and grounded, cached file summaries. added, uncommitted. +1022 / -0 lines
- contextbridge/gui/diagrams.py — Native diagram rendering, independent of graph discovery and project operations. added, uncommitted. +378 / -156 lines
- contextbridge/project_map.py — Bounded static Python import map. added, uncommitted. +916 / -9 lines
- contextbridge/core/watcher.py — Explicit, bounded-cost polling of file metadata. added. +265 / -0 lines
- contextbridge/synthesis/facts.py — Deterministic project facts and file ranking for compact handoffs. added. +209 / -0 lines
- contextbridge/synthesis/llm_client.py — Compatibility imports for clients that used the original synthesis module. modified. +39 / -161 lines
<!-- contextbridge:end -->
