import pytest

from contextbridge.chat_bridge import build_chat_packet


def test_packet_keeps_goal_and_essentials_within_budget():
    packet = build_chat_packet(
        "User: old details\n" * 3000 + "\nAssistant: latest answer",
        "Finish the plan",
        "Budget is 300",
        2000,
    )
    assert len(packet.text) <= 2000
    assert "Finish the plan" in packet.text and "Budget is 300" in packet.text
    assert "latest answer" in packet.text and packet.omitted_chars > 0
    assert "## Decisions" in packet.text and "## Next steps" in packet.text


def test_short_chat_preserved_and_secret_redacted():
    packet = build_chat_packet("User: key AKIAIOSFODNN7EXAMPLE\nAssistant: hello", "", "")
    assert "AKIAIOSFODNN7EXAMPLE" not in packet.text
    assert "Latest assistant response — hello." in packet.text and packet.omitted_chars == 0


def test_rejects_oversized_or_empty_inputs():
    with pytest.raises(ValueError):
        build_chat_packet("x" * 100001, "", "")
    with pytest.raises(ValueError):
        build_chat_packet("", "", "")
    with pytest.raises(ValueError):
        build_chat_packet("hello", "x" * 1001, "")
