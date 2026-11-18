"""Realistic conversation exports and complete, chronological extraction."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from contextbridge.chat_bridge import (
    CHAT_TITLES,
    chat_sections,
    chat_warnings,
    condense_chat,
    parse_turns,
)
from contextbridge.main import app
from contextbridge.synthesis.token_counter import count_tokens

FIXTURE = Path(__file__).parent / "fixtures" / "flutter_lab_chat.txt"


def test_flutter_lab_export_joins_wrapped_turns_and_keeps_progression():
    text = FIXTURE.read_text()
    turns = parse_turns(text)
    assert len(turns) == 10
    assert [turn.role for turn in turns] == ["user", "assistant"] * 5
    assert "Flutter project using both the Command Line Interface" in turns[3].text
    sections = chat_sections(text)
    assert "observation" in sections["Goal"][0] and "minimal as possible" in sections["Goal"][0]
    assert (
        "Exercise 4" in sections["Current state"][-1]
        and "Exercise 5" in sections["Current state"][-1]
    )
    assert "Navigator" in sections["Current state"][0]
    done = "\n".join(sections["Done"])
    assert done.index("project creation instructions") < done.index("interactive HTML simulation")
    assert "I will now" not in done
    assert not any("preview and test all 6" in line for line in sections["Next steps"])
    assert sections["Files"] == ["`flutter_outputs_preview.html`", "`flutter_lab_observations.md`"]
    output = condense_chat(text)
    assert "https://" not in output and "(trimmed)" not in output
    assert all("## " + title in output for title in CHAT_TITLES)
    assert not chat_warnings(text)


@pytest.mark.parametrize("budget", [200, 350, 700, 2000])
def test_budget_keeps_whole_statements_instead_of_fragments(budget):
    sections = chat_sections(FIXTURE.read_text())
    output = condense_chat(FIXTURE.read_text(), budget=budget)
    assert count_tokens(output) <= budget and "…" not in output
    allowed = {line for values in sections.values() for line in values}
    for line in output.splitlines():
        if line.startswith("- "):
            body = line[2:]
            assert body in allowed or body.endswith("details exceed this budget.")


@pytest.mark.parametrize(
    "user,assistant",
    [
        ("User:", "Assistant:"),
        ("User prompt:", "Response:"),
        ("You said", "ChatGPT said:"),
        ("Human:", "AI:"),
        ("**User:**", "**Assistant:**"),
    ],
)
def test_export_speaker_labels_and_wrapped_sentences(user, assistant):
    text = (
        f"{user}\nPlease create a responsive Flutter\napplication with navigation.\n"
        f"{assistant}\nI have created the responsive Flutter\napplication in main.dart.\n"
        f"{user}\nPlease add a settings screen and\nexplain the navigation steps?\n"
    )
    sections = chat_sections(text)
    assert len(parse_turns(text)) == 3
    assert "responsive Flutter application with navigation." in sections["Goal"][0]
    assert "No completed work" in sections["Done"][0]  # already included in Current state
    assert "settings screen and explain the navigation steps?" in sections["Current state"][-1]
    assert "Latest assistant response" in sections["Current state"][0]
    assert any("settings screen" in line for line in sections["Next steps"])


def test_dedupe_noise_urls_and_chronological_completion_claims():
    text = (
        "User: Please help me finish the Flutter lab report.\n"
        "Assistant: I have added the installation instructions.\n"
        "I have added the installation instructions.\n"
        "<tool>private tool output with https://example.test</tool>\n"
        "User: Please add the navigation exercises as well.\n"
        "Assistant: I have created navigation.html.\n"
        "Tool call: render\n"
        "User: Please check the final report and explain the remaining errors?\n"
        "Assistant: I have updated report.md. Next steps: Review the diagrams.\n"
        "https://example.test/report.md\n"
    )
    sections = chat_sections(text)
    output = condense_chat(text)
    assert len(sections["Done"]) == 2
    assert "installation" in sections["Done"][0]
    assert "navigation.html" in sections["Done"][1]
    assert "report.md" not in "\n".join(sections["Done"])
    assert "private tool output" not in output and "Tool call" not in output
    assert "https://" not in output
    assert any("Review the diagrams" in line for line in sections["Next steps"])


def test_short_or_unstructured_input_warns_on_stderr():
    runner = CliRunner()
    for text in ("User: Help\nAssistant: Hello", "An unstructured note about Flutter. " * 40):
        result = runner.invoke(app, ["import-chat"], input=text)
        assert result.exit_code == 0, result.output
        assert "does not look like a conversation" in result.stderr
        assert result.stdout.startswith("# ContextBridge handoff")


def test_secret_redaction_in_joined_turns():
    text = (
        "User prompt: Please generate the next Flutter exercise\n"
        "using key AKIAIOSFODNN7EXAMPLE.\n"
        "Response: I have created exercise.dart.\n"
    )
    output = condense_chat(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in output and "REDACTED" in output


def test_finished_earlier_next_step_is_not_repeated_at_the_end():
    text = (
        "User: Next: Add installation steps.\n"
        "Assistant: I have added installation steps.\n"
        "User: Please create the responsive HTML simulator now.\n"
        "Assistant: I have created simulator.html. Should we add keyboard navigation?\n"
    )
    sections = chat_sections(text)
    assert not any("installation" in line for line in sections["Next steps"])
    assert any("keyboard navigation?" in line for line in sections["Next steps"])


def test_unstructured_text_is_quoted_without_urls():
    output = condense_chat(
        "Please use notes.md and see https://example.test/private.md for details."
    )
    assert "Imported text" in output and "notes.md" in output
    assert "https://" not in output and "private.md" not in output


def test_flutter_goal_neutral_done_and_no_closing_next_steps():
    sections = chat_sections(FIXTURE.read_text())
    assert len(" ".join(sections["Goal"]).split()) <= 40
    assert "Questions:" not in sections["Goal"][0]
    assert "minimal" in sections["Goal"][0]
    assert all(not line.startswith(("I ", "I've", "We ")) for line in sections["Done"])
    assert any(line.startswith("Added project creation") for line in sections["Done"])
    assert "Updated the lab report document." not in sections["Done"]
    assert any("Steps & Procedure" in line for line in sections["Done"])
    assert not any("successfully updated both" in line.lower() for line in sections["Done"])
    assert sections["Next steps"] == ["No explicit next steps or final open questions were stated."]


def test_flutter_explicit_question_survives_closing_preview():
    sections = chat_sections(FIXTURE.read_text() + "\nShould we add keyboard navigation?")
    assert sections["Next steps"] == ["Should we add keyboard navigation?"]


def test_flutter_labeled_preview_is_not_a_next_step():
    sections = chat_sections(FIXTURE.read_text() + "\nNext steps: You can now preview the report.")
    assert sections["Next steps"] == ["No explicit next steps or final open questions were stated."]


def test_flutter_long_goal_keeps_the_request_before_its_details():
    text = FIXTURE.read_text().replace(
        "solution as minimal as possible with an output.",
        "solution as minimal as possible with an output, including "
        + "navigation widgets layouts responsive breakpoints " * 12
        + ".",
    )
    goal = chat_sections(text)["Goal"][0]
    assert len(goal.split()) <= 40
    assert "Flutter" in goal or "flutter" in goal
    assert "Questions:" not in goal
