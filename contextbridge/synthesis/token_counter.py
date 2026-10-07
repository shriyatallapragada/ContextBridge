"""Token counting with tiktoken (``cl100k_base``), degrading to an estimate.

Counts are an approximation for non-OpenAI models; the UI labels them "approx.".
tiktoken downloads its encoding on first use (with no timeout), so loading runs in a
background thread with a deadline: offline, we fall back to ~4 characters per token
instead of hanging.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from functools import cache
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir

log = logging.getLogger("contextbridge")

ENCODING_NAME = "cl100k_base"
LOAD_TIMEOUT_SECONDS = 5.0
CHARS_PER_TOKEN = 4
APPROX_LABEL = "approx."


def _cache_dir() -> Path:
    return Path(user_cache_dir("contextbridge", appauthor=False)) / "tiktoken"


@cache
def _encoding() -> Any | None:
    """The tiktoken encoding, or None if it cannot be loaded in time."""
    if os.environ.get("CB_TOKENIZER") == "estimate":
        return None
    # A persistent cache: the default lives in a temp dir that the OS clears.
    if "TIKTOKEN_CACHE_DIR" not in os.environ:
        cache_dir = _cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["TIKTOKEN_CACHE_DIR"] = str(cache_dir)
    result: list[Any] = []

    def load() -> None:
        try:
            import tiktoken

            result.append(tiktoken.get_encoding(ENCODING_NAME))
        except Exception:
            log.info("tokens: cannot load %s; estimating", ENCODING_NAME, exc_info=True)

    thread = threading.Thread(target=load, daemon=True)
    thread.start()
    thread.join(LOAD_TIMEOUT_SECONDS)
    if not result:
        log.info("tokens: %s unavailable; using a character estimate", ENCODING_NAME)
        return None
    return result[0]


def is_estimate() -> bool:
    """True when counts come from the character heuristic rather than tiktoken."""
    return _encoding() is None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _encoding()
    if enc is None:
        return math.ceil(len(text) / CHARS_PER_TOKEN)
    return len(enc.encode(text, disallowed_special=()))


def truncate_to_tokens(text: str, max_tokens: int, *, marker: str = "") -> str:
    """Cut ``text`` to at most ``max_tokens`` (including ``marker`` if it is cut)."""
    if max_tokens <= 0:
        return ""
    if count_tokens(text) <= max_tokens:
        return text
    budget = max(max_tokens - count_tokens(marker), 0)
    enc = _encoding()
    if enc is None:
        cut = text[: budget * CHARS_PER_TOKEN]
    else:
        cut = enc.decode(enc.encode(text, disallowed_special=())[:budget])
    # Prefer a line boundary so we do not leave half a line behind.
    if "\n" in cut[len(cut) // 2 :]:
        cut = cut[: cut.rindex("\n")]
    return cut.rstrip() + marker
