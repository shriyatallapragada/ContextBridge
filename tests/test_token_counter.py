from __future__ import annotations

import pytest

from contextbridge.synthesis import token_counter as tc


def test_counts_and_truncation() -> None:
    assert tc.count_tokens("") == 0
    assert tc.count_tokens("hello world") > 0
    text = "\n".join(f"line {i} with a few words" for i in range(200))
    cut = tc.truncate_to_tokens(text, 50, marker="\n[cut]")
    assert tc.count_tokens(cut) <= 50
    assert cut.endswith("\n[cut]")
    assert tc.truncate_to_tokens("short", 50) == "short"
    assert tc.truncate_to_tokens("anything", 0) == ""


def test_estimate_mode_when_encoding_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline first run: tiktoken cannot fetch its encoding, so counts are estimated."""
    monkeypatch.setenv("CB_TOKENIZER", "estimate")
    tc._encoding.cache_clear()
    try:
        assert tc.is_estimate()
        assert tc.count_tokens("x" * 40) == 10
        cut = tc.truncate_to_tokens("word " * 100, 10)
        assert tc.count_tokens(cut) <= 10
    finally:
        monkeypatch.delenv("CB_TOKENIZER")
        tc._encoding.cache_clear()


def test_slow_encoding_load_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    import tiktoken

    release = threading.Event()

    def hang(name: str) -> object:
        release.wait(5)
        raise RuntimeError("never loaded")

    monkeypatch.setattr(tiktoken, "get_encoding", hang)
    monkeypatch.setattr(tc, "LOAD_TIMEOUT_SECONDS", 0.2)
    tc._encoding.cache_clear()
    try:
        assert tc.is_estimate()  # returned after the deadline instead of hanging
    finally:
        release.set()
        tc._encoding.cache_clear()
