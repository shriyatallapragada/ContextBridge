"""The look: palette from the swatches, readable contrast, and the plain-language wording."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from contextbridge.gui import theme, wording
from contextbridge.storage.db import TestRun

# ----- palette ----------------------------------------------------------------------------


def test_palette_is_the_four_swatches() -> None:
    assert theme.RED_CHAI == "#632626"
    assert theme.MASALA == "#9D5353"
    assert theme.CINNAMON_TOAST == "#BF8B67"
    assert theme.CARDAMOM == "#DBCB96"


def test_roles_use_the_swatches() -> None:
    assert theme.SIDEBAR == theme.RED_CHAI and theme.TEXT == theme.RED_CHAI
    assert theme.PRIMARY == theme.MASALA
    assert theme.ACCENT == theme.CINNAMON_TOAST
    assert theme.ON_DARK_MUTED == theme.CARDAMOM


@pytest.mark.parametrize(("fg", "bg", "name"), theme.TEXT_PAIRS)
def test_every_text_pair_meets_wcag_aa(fg: str, bg: str, name: str) -> None:
    assert theme.contrast_ratio(fg, bg) >= 4.5, f"{name}: {fg} on {bg}"


def test_contrast_helper_known_values() -> None:
    assert theme.contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert theme.contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    assert theme.mix("#000000", "#FFFFFF", 0.5) == "#808080"


def test_stylesheet_is_complete() -> None:
    images = {"check": "/tmp/c.png", "radio": "/tmp/r.png", "chevron": "/tmp/v.png"}
    css = theme.build_stylesheet(images)
    assert all(path in css for path in images.values())
    assert css.count("{") == css.count("}")  # every rule is closed
    for colour in (theme.PRIMARY, theme.SIDEBAR, theme.BG, theme.SURFACE):
        assert colour in css
    assert "BLACK_MIX" not in css and "{{" not in css and "}}" not in css
    assert theme.build_stylesheet()  # works without indicator images


# ----- wording -------------------------------------------------------------------------------


def test_note_kinds_cover_every_stored_tag() -> None:
    assert {k.key for k in wording.NOTE_KINDS} == {"note", "bug", "architecture", "blocker", "todo"}
    assert wording.NOTE_LABELS["architecture"] == "Decision"
    assert wording.NOTE_LABELS["blocker"] == "Stuck on"


def test_agents_match_adapters() -> None:
    from contextbridge.adapters.registry import ADAPTERS

    assert {a.key for a in wording.AGENTS} == set(ADAPTERS)
    files = {a.key: a.file for a in wording.AGENTS}
    assert files == {
        "claude": "CLAUDE.md",
        "cursor": ".cursor/rules/handoff.mdc",
        "generic": "HANDOFF.md",
    }


def test_words_and_size_sentences() -> None:
    assert wording.words(0) == "no words"
    assert wording.words(240) == "about 180 words"
    assert "shorter" in wording.size_sentence(900, 240) and "73%" in wording.size_sentence(900, 240)
    small = wording.size_sentence(65, 173)
    assert "small" in small and "shorter" not in small  # never claims savings it didn't make
    assert "token" not in wording.size_sentence(900, 240).lower()


def test_how_written() -> None:
    assert "No AI was needed" in wording.how_written("deterministic", [])
    assert "wasn't reachable" in wording.how_written("deterministic", [("ollama", "down")])
    assert "qwen" in wording.how_written("ollama", [], "qwen")


def test_when_is_friendly() -> None:
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    def ago(**kw: float) -> str:
        return wording.when((now - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ"), now=now)

    assert ago(seconds=5) == "just now"
    assert ago(minutes=5) == "5 min ago"
    assert ago(hours=1) == "1 hour ago" and ago(hours=3) == "3 hours ago"
    assert ago(hours=30) == "yesterday"
    assert ago(days=4) == "4 days ago"
    assert ago(days=40) not in ("yesterday", "just now") and "ago" not in ago(days=40)


def _run(*, exit_code: int, passed: int | None, failed: int | None) -> TestRun:
    return TestRun("r", "s", "pytest", exit_code, passed, failed, 0, [], "2026-10-10T12:00:00Z")


def test_test_summary() -> None:
    assert wording.test_summary(None) == ("No tests run yet", None)
    assert wording.test_summary(_run(exit_code=1, passed=3, failed=2)) == ("2 tests failing", False)
    assert wording.test_summary(_run(exit_code=1, passed=3, failed=1)) == ("1 test failing", False)
    assert wording.test_summary(_run(exit_code=0, passed=12, failed=0)) == (
        "All 12 tests passing",
        True,
    )
    assert wording.test_summary(_run(exit_code=0, passed=None, failed=None))[1] is True
    assert wording.test_summary(_run(exit_code=2, passed=None, failed=None))[1] is False


def test_plural() -> None:
    assert wording.plural(1, "note") == "1 note" and wording.plural(2, "note") == "2 notes"
    assert wording.plural(0, "match", "matches") == "0 matches"
