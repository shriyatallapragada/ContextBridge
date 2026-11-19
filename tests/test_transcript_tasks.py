"""Chat Bridge transcript parsing: fixed rules, same input, same tasks, 0 tokens."""

from __future__ import annotations

from contextbridge.core.transcript_tasks import MAX_TASKS, parse_transcript_tasks

TRANSCRIPT = """\
User: we need to ship the dashboard

Assistant: sure. Next steps:
- wire the React tabs
- add a timeline view

Todo: review the graph layout
- [ ] write tests for the parser
- [x] scaffold the FastAPI server
ACTION: publish the MCP bridge

```python
- [ ] this one is inside a code fence
```

Follow-ups:
- record the decision in SQLite
"""


def test_extracts_checkboxes_markers_and_section_bullets() -> None:
    drafts = parse_transcript_tasks(TRANSCRIPT)

    by_kind: dict[str, list[str]] = {}
    for draft in drafts:
        by_kind.setdefault(draft.kind, []).append(draft.content)

    assert by_kind["section"] == [
        "wire the React tabs",
        "add a timeline view",
        "record the decision in SQLite",
    ]
    assert by_kind["marker"] == ["review the graph layout", "publish the MCP bridge"]
    assert by_kind["checkbox"] == ["write tests for the parser", "scaffold the FastAPI server"]
    assert [d.status for d in drafts if d.kind == "checkbox"] == ["open", "resolved"]


def test_source_line_numbers_point_back_to_the_transcript() -> None:
    drafts = parse_transcript_tasks(TRANSCRIPT)
    lines = {d.content: d.line for d in drafts}

    assert (
        lines["wire the React tabs"] == TRANSCRIPT.splitlines().index("- wire the React tabs") + 1
    )
    assert lines["scaffold the FastAPI server"] == 9


def test_code_fences_are_skipped_and_duplicates_collapse() -> None:
    drafts = parse_transcript_tasks(TRANSCRIPT)
    contents = [d.content for d in drafts]

    assert "this one is inside a code fence" not in contents
    assert len(contents) == len(set(contents))

    repeated = parse_transcript_tasks("- [ ] ship it\n- [ ] ship it\n- [x] ship it")
    assert [d.content for d in repeated] == ["ship it"]
    assert repeated[0].status == "open"  # first occurrence wins


def test_parsing_is_deterministic_and_handles_empty_input() -> None:
    assert parse_transcript_tasks("") == []
    assert parse_transcript_tasks("   \n\n") == []
    first = parse_transcript_tasks(TRANSCRIPT)
    second = parse_transcript_tasks(TRANSCRIPT)
    assert [d.to_dict() for d in first] == [d.to_dict() for d in second]


def test_output_is_capped_and_sanitized() -> None:
    many = "\n".join(f"- [ ] task number {i}" for i in range(MAX_TASKS + 40))
    assert len(parse_transcript_tasks(many)) == MAX_TASKS

    drafts = parse_transcript_tasks("- [ ] use sk-live-abcdefghij1234567890 to connect")
    assert "sk-live-abcdefghij1234567890" not in drafts[0].content
    assert "[REDACTED" in drafts[0].content


def test_plain_prose_without_task_markers_yields_nothing() -> None:
    transcript = "User: hello\nAssistant: hi, the build is green today.\n"
    assert parse_transcript_tasks(transcript) == []
