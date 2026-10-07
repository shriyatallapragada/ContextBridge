"""Compressor tests: LLM calls are mocked with a scripted fake client (no network)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from contextbridge.config import Config
from contextbridge.storage.db import Database
from contextbridge.synthesis.compressor import (
    DETERMINISTIC,
    TRUNCATION_NOTE,
    build_handoff,
    clean_output,
    compress,
    deterministic_summary,
    format_ratio,
)
from contextbridge.synthesis.context_builder import SessionContext, build_context
from contextbridge.synthesis.llm_client import (
    LLMClientError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from contextbridge.synthesis.token_counter import count_tokens


@dataclass
class FakeClient:
    """Returns (or raises) scripted responses in order and records every call."""

    responses: list[str | Exception]
    provider: str = "ollama"
    model: str = "fake-model"
    cloud: bool = False
    calls: list[tuple[str, str, int]] = field(default_factory=list)

    @property
    def is_cloud(self) -> bool:
        return self.cloud

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        self.calls.append((system, user, max_tokens))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _long(n_lines: int) -> str:
    return "\n".join(
        f"- point {i}: the cart total now uses integer cents everywhere" for i in range(n_lines)
    )


@pytest.fixture
def ctx(tmp_path: Path) -> SessionContext:
    db = Database.open(tmp_path / "state.db")
    session = db.create_session("feature/cart")
    db.add_decision(session.id, "Prices are stored in cents, never floats", "architecture")
    db.add_decision(session.id, "Checkout 500s on an empty cart", "blocker")
    db.add_decision(session.id, "Add discount codes", "todo")
    db.add_decision(session.id, "Looked at the payment SDK docs", "note")
    db.add_file_change(
        session.id,
        "cart.py",
        "modified",
        commit_sha="a" * 40,
        symbols=[
            {
                "name": "Cart.total",
                "kind": "method",
                "change": "modified",
                "signature": "def total(self)",
            }
        ],
        diff_snippet=(
            "@@ -1,2 +1,2 @@ class Cart:\n-    return sum(x)\n+    return sum(i.price for i in x)"
        ),
    )
    db.add_test_run(
        session.id,
        "pytest -q",
        1,
        passed=4,
        failed=1,
        skipped=0,
        failures=[{"test": "tests/test_cart.py::test_empty", "message": "500 on empty cart"}],
    )
    context = build_context(db, session, Config(), project="shop", observer=None)
    db.close()
    return context


# ----- LLM path ------------------------------------------------------------------------


def test_llm_success_uses_model_output(ctx: SessionContext) -> None:
    client = FakeClient(["- Cart.total now sums integer cents\n- Blocker: empty cart 500s"])
    result = compress(ctx, Config(), summary_budget=300, clients=[client])
    assert result.method == "ollama"
    assert result.summary.startswith("- Cart.total")
    system, user, _ = client.calls[0]
    assert system == Config().handoff.system_prompt
    assert "Prices are stored in cents" in user and "<context>" in user


def test_over_length_output_retries_once_with_stricter_prompt(ctx: SessionContext) -> None:
    client = FakeClient([_long(200), "- short and dense"])
    result = compress(ctx, Config(), summary_budget=300, clients=[client])
    assert result.summary == "- short and dense"
    assert [a.outcome for a in result.attempts] == ["retried"]
    assert len(client.calls) == 2
    assert "too long" in client.calls[1][1]


def test_still_over_length_after_retry_is_truncated(ctx: SessionContext) -> None:
    client = FakeClient([_long(200), _long(150)])
    result = compress(ctx, Config(), summary_budget=300, clients=[client])
    assert result.method == "ollama"
    assert count_tokens(result.summary) <= 300
    assert result.summary.endswith(TRUNCATION_NOTE)
    assert [a.outcome for a in result.attempts] == ["truncated"]
    assert len(client.calls) == 2  # exactly one retry


def test_timeout_falls_back_to_deterministic(ctx: SessionContext) -> None:
    client = FakeClient([LLMTimeoutError("Ollama timed out after 60s")])
    result = compress(ctx, Config(), summary_budget=300, clients=[client])
    assert result.method == DETERMINISTIC
    assert result.attempts[0].outcome == "Ollama timed out after 60s"
    assert "## Blockers" in result.summary


def test_unreachable_local_then_cloud_with_notice(ctx: SessionContext) -> None:
    local = FakeClient([LLMUnavailableError("Ollama is not reachable")])
    cloud = FakeClient(["- from the cloud"], provider="groq", model="llama", cloud=True)
    notices: list[str] = []
    result = compress(
        ctx, Config(), summary_budget=300, clients=[local, cloud], notify=notices.append
    )
    assert result.method == "groq"
    assert len(notices) == 1
    assert "groq" in notices[0] and "sanitized" in notices[0] and "ollama unavailable" in notices[0]


def test_unexpected_client_exception_never_breaks_handoff(ctx: SessionContext) -> None:
    client = FakeClient([RuntimeError("bug in SDK")])
    result = compress(ctx, Config(), summary_budget=300, clients=[client])
    assert result.method == DETERMINISTIC
    assert "unexpected error" in result.attempts[0].outcome


def test_empty_response_falls_through(ctx: SessionContext) -> None:
    result = compress(ctx, Config(), summary_budget=300, clients=[FakeClient(["   "])])
    assert result.method == DETERMINISTIC


def test_retry_failure_falls_back_to_truncating_first_answer(ctx: SessionContext) -> None:
    client = FakeClient([_long(200), LLMClientError("server error")])
    result = compress(ctx, Config(), summary_budget=300, clients=[client])
    # The provider failed on the retry: the next option (deterministic) is used.
    assert result.method == DETERMINISTIC


def test_prompt_is_sanitized_before_sending(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "s.db")
    session = db.create_session("main")
    db.add_decision(session.id, "temp note", "note")
    context = build_context(db, session, Config(), project="p", observer=None)
    db.close()
    # Simulate a secret slipping into the context (e.g. a future source forgetting to sanitize).
    context.items[0].text += "\nkey AKIAIOSFODNN7EXAMPLE"
    client = FakeClient(["- ok"])
    compress(context, Config(), summary_budget=200, clients=[client])
    assert "AKIAIOSFODNN7EXAMPLE" not in client.calls[0][1]


def test_no_llm_and_provider_none_skip_clients(ctx: SessionContext) -> None:
    assert compress(ctx, Config(), summary_budget=300, no_llm=True).method == DETERMINISTIC
    cfg = Config()
    cfg.llm.provider = "none"
    assert compress(ctx, cfg, summary_budget=300).method == DETERMINISTIC


# ----- output cleanup -------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Here is the handoff summary:\n- a\n- b",
        "Sure! Here's a concise summary:\n\n- a\n- b",
        "<think>let me plan this out</think>\n- a\n- b",
        "```markdown\n- a\n- b\n```",
        "\n\n- a\n- b\n",
    ],
)
def test_clean_output_strips_preambles(raw: str) -> None:
    assert clean_output(raw) == "- a\n- b"


def test_clean_output_keeps_real_content() -> None:
    text = "## Done\n- Here is where totals are computed: Cart.total"
    assert clean_output(text) == text


# ----- deterministic fallback --------------------------------------------------------------


def test_deterministic_has_all_sections(ctx: SessionContext) -> None:
    text = deterministic_summary(ctx, 600)
    for section in (
        "Goal / Branch",
        "Done",
        "Files & symbols",
        "Decisions",
        "Failing tests",
        "Blockers",
        "Next steps",
    ):
        assert f"## {section}" in text
    assert "cart.py (modified): Cart.total" in text
    assert "tests/test_cart.py::test_empty: 500 on empty cart" in text
    assert "Checkout 500s on an empty cart" in text
    assert "Add discount codes" in text


def test_deterministic_drops_low_priority_first(ctx: SessionContext) -> None:
    full = deterministic_summary(ctx, 600)
    tight = deterministic_summary(ctx, count_tokens(full) - 10)
    assert "(note) Looked at the payment SDK docs" in full
    assert "(note) Looked at the payment SDK docs" not in tight  # notes go first
    assert "Checkout 500s on an empty cart" in tight  # blockers stay
    assert "Prices are stored in cents" in tight  # architecture stays


@pytest.mark.parametrize("budget", [40, 80, 150, 300])
def test_deterministic_respects_budget(ctx: SessionContext, budget: int) -> None:
    assert count_tokens(deterministic_summary(ctx, budget)) <= budget


# ----- payload -------------------------------------------------------------------------


@pytest.mark.parametrize("cap", [120, 200, 600])
def test_payload_respects_hard_cap(ctx: SessionContext, cap: int) -> None:
    cfg = Config()
    cfg.handoff.hard_cap_tokens = cap
    cfg.handoff.target_tokens = min(cfg.handoff.target_tokens, cap)
    for clients in ([], [FakeClient([_long(300), _long(300)])]):
        handoff = build_handoff(ctx, cfg, from_agent="claude", to_agent="cursor", clients=clients)
        assert handoff.compressed_tokens <= cap
        assert count_tokens(handoff.payload) == handoff.compressed_tokens


def test_payload_layout_and_counts(ctx: SessionContext) -> None:
    handoff = build_handoff(ctx, Config(), from_agent="claude", to_agent="cursor", clients=[])
    lines = handoff.payload.splitlines()
    assert lines[0] == "# ContextBridge handoff"
    assert (
        lines[1].startswith("From: claude -> To: cursor | ") and "Branch: feature/cart" in lines[1]
    )
    assert "## Active files\n- cart.py" in handoff.payload
    assert handoff.payload.rstrip().endswith("next steps above.")
    assert handoff.raw_tokens == ctx.raw_tokens > 0
    assert handoff.compression_ratio == pytest.approx(
        1 - handoff.compressed_tokens / handoff.raw_tokens
    )


def test_format_ratio() -> None:
    assert format_ratio(0.973) == "97.3%"
    assert format_ratio(None) == "n/a"
