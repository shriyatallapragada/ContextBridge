"""Compressor tests: LLM calls are mocked with a scripted fake client (no network)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from contextbridge.config import Config
from contextbridge.storage.db import Database
from contextbridge.synthesis.compressor import (
    DETERMINISTIC,
    build_handoff,
    clean_output,
    compress,
    deterministic_summary,
    format_ratio,
)
from contextbridge.synthesis.context_builder import SessionContext, build_context
from contextbridge.synthesis.llm_client import (
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
        diff_snippet="+3 / -2 lines",
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


# Model responses must preserve the supplied evidence and section layout.


def _echo(ctx, budget=600):
    import json

    text = deterministic_summary(ctx, budget)
    return json.dumps(
        {"lines": [line + "." for line in text.splitlines() if line.startswith("- ")]}
    )


def test_llm_success_preserves_facts(ctx):
    client = FakeClient([_echo(ctx)])
    result = compress(ctx, Config(), summary_budget=600, clients=[client])
    assert result.method == "ollama"
    assert "Prices are stored in cents" in result.summary
    assert "Cart.total" not in result.summary


def test_invalid_model_output_retries_then_falls_back(ctx):
    client = FakeClient(["invented", "invented again"])
    result = compress(ctx, Config(), summary_budget=600, clients=[client])
    assert result.method == DETERMINISTIC
    assert result.summary == deterministic_summary(ctx, 600)
    assert len(client.calls) == 2


def test_valid_retry_is_accepted(ctx):
    client = FakeClient(["invalid", _echo(ctx)])
    result = compress(ctx, Config(), summary_budget=600, clients=[client])
    assert result.method == "ollama" and result.attempts[0].outcome == "retried"


def test_timeout_and_provider_bug_fall_back(ctx):
    for error in (LLMTimeoutError("timed out"), RuntimeError("SDK bug")):
        result = compress(ctx, Config(), summary_budget=600, clients=[FakeClient([error])])
        assert result.method == DETERMINISTIC
        assert "## Open problems" in result.summary


def test_local_failure_then_cloud_preserves_facts(ctx):
    local = FakeClient([LLMUnavailableError("offline")])
    cloud = FakeClient([_echo(ctx)], provider="groq", cloud=True)
    notices = []
    result = compress(
        ctx, Config(), summary_budget=600, clients=[local, cloud], notify=notices.append
    )
    assert result.method == "groq" and "sanitized" in notices[0]


def test_model_cannot_invent_or_reverse_a_decision(ctx):
    import json

    valid = json.loads(_echo(ctx))
    valid["lines"] = [line.replace("never floats", "always floats") for line in valid["lines"]]
    client = FakeClient([json.dumps(valid), json.dumps(valid)])
    result = compress(ctx, Config(), summary_budget=600, clients=[client])
    assert result.method == DETERMINISTIC
    assert "always floats" not in result.summary


def test_none_skips_model(ctx):
    config = Config()
    config.llm.provider = "none"
    assert compress(ctx, config, summary_budget=600).method == DETERMINISTIC


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


def test_deterministic_sections_and_no_test_function_names(ctx):
    from contextbridge.synthesis.document import TITLES

    text = deterministic_summary(ctx, 600)
    assert all("## " + title in text for title in TITLES)
    assert "(trimmed)" not in text
    assert "cart.py" in text and "+3 / -2 lines" in text
    assert "Cart.total" not in text and "test_empty" not in text
    assert "Checkout 500s" in text and "Add discount codes" in text


@pytest.mark.parametrize("budget", [120, 200, 300, 600, 2000])
def test_deterministic_reserves_essential_sections(ctx, budget):
    from contextbridge.synthesis.document import TITLES

    text = deterministic_summary(ctx, budget)
    assert count_tokens(text) <= budget
    assert "(trimmed)" not in text
    assert all("## " + title + "\n- " in text for title in TITLES)


@pytest.mark.parametrize("cap", [120, 200, 600, 2000])
def test_payload_respects_hard_cap(ctx, cap):
    config = Config()
    config.handoff.hard_cap_tokens = config.handoff.target_tokens = cap
    handoff = build_handoff(ctx, config, clients=[])
    assert count_tokens(handoff.payload) == handoff.compressed_tokens <= cap
    assert "(trimmed)" not in handoff.payload


def test_format_ratio():
    assert format_ratio(0.973) == "97.3%"
    assert format_ratio(None) == "n/a"
