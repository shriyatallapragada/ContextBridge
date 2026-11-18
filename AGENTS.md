<!-- contextbridge:start -->
# ContextBridge handoff
From: claude -> To: codex | 2026-11-18T03:30:00Z | Branch: phase-5-gui

## Goal / Branch
- Branch `phase-5-gui` (ContextBridge)
- Latest commit: Checkpoint before engine refactor

## Done
- (trimmed)

## Files & symbols
- tests/test_project_map.py (added): test_map_skips_external_symlinks_large_and_invalid_files (added), test_static_imports_and_relative_imports_without_execution (added)
- tests/test_phase4_cli.py (modified): test_secrets_never_reach_the_files_or_database, test_unknown_agent_fails_before_any_work
- tests/test_gui_theme.py (modified): test_guide_section_contrast (added), test_agents_match_adapters
- tests/test_gui.py (modified): test_chat_bridge_without_project (added), test_chat_import_pdf_export_and_cancel_inflight (added), test_guide_flowcharts_visible_and_refresh_preserves_selection (added), test_guide_rejects_documents_outside_project (added), test_guide_renders_markdown_and_workflow (added), test_project_guide_reads_documentation (added)
- tests/test_engine_boundary.py (added): test_chat_service_import_and_prepare_need_no_project (added), test_cli_notes_status_and_search_use_service (added), test_commit_capture_is_available_through_engine (added), test_document_reads_are_bounded_sanitized_and_confined (added), test_engine_works_when_frontend_imports_are_blocked (added)
- tests/test_chat_pdf.py (added): app (added), test_failed_pdf_export_preserves_existing_file (added), test_pdf_roundtrip_pagination_and_mixed_pages (added), test_scanned_encrypted_invalid_and_page_limit (added), test_text_import_unchanged_and_empty_file_rejected (added)
- tests/test_chat_bridge.py (added): test_packet_keeps_goal_and_essentials_within_budget (added), test_rejects_oversized_or_empty_inputs (added), test_short_chat_preserved_and_secret_redacted (added)

## Decisions
- (trimmed)

## Failing tests
- Last run: `uv run pytest -q` exited 0 (490 passed, 0 failed, 0 skipped)

## Blockers
- None

## Next steps
- (trimmed)

## Active files
- tests/test_project_map.py
- tests/test_phase4_cli.py
- tests/test_gui_theme.py
- tests/test_gui.py
- tests/test_engine_boundary.py
- tests/test_chat_pdf.py
- tests/test_chat_bridge.py
- tests/test_adapters.py
- pyproject.toml
- launch.command

To resume: read the active files, check `cb status` for live state, and continue from the next steps above.
<!-- contextbridge:end -->
