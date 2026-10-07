"""Compress a SessionContext into a handoff payload within the token cap.

The LLM path sends the (sanitized, budgeted) context with the configured system
prompt, strips any preamble, and validates length: over budget means one retry with a
stricter instruction, then line-wise truncation. If no LLM is usable, a deterministic
template is filled by priority. That path is offline and cannot fail.

The whole payload (header + summary + active files + resume line) stays within
``handoff.hard_cap_tokens``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from contextbridge.config import Config
from contextbridge.core.sanitizer import sanitize
from contextbridge.synthesis.context_builder import (
    SessionContext,
    describe_test_run,
    format_symbols,
)
from contextbridge.synthesis.llm_client import LLMClient, LLMClientError, candidates
from contextbridge.synthesis.token_counter import count_tokens

log = logging.getLogger("contextbridge")

DETERMINISTIC = "deterministic"
ACTIVE_FILES_IN_PAYLOAD = 10
MIN_SUMMARY_TOKENS = 80
TRUNCATION_NOTE = "- [summary truncated to fit the token cap]"
RESUME_LINE = (
    "To resume: read the active files, check `cb status` for live state, "
    "and continue from the next steps above."
)


@dataclass
class Attempt:
    provider: str
    outcome: str  # "ok" | "retried" | "truncated" | error text


@dataclass
class CompressionResult:
    summary: str
    method: str  # "ollama" | "groq" | "openai" | "deterministic"
    attempts: list[Attempt] = field(default_factory=list)


@dataclass
class Handoff:
    payload: str
    method: str
    raw_tokens: int
    compressed_tokens: int
    attempts: list[Attempt]

    @property
    def compression_ratio(self) -> float | None:
        """``1 - compressed/raw`` (None when there is no raw context to compare)."""
        if self.raw_tokens <= 0:
            return None
        return 1 - self.compressed_tokens / self.raw_tokens


def format_ratio(ratio: float | None) -> str:
    return "n/a" if ratio is None else f"{ratio * 100:.1f}%"


# ----- output cleanup ------------------------------------------------------------------

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```[\w-]*\n(.*?)\n```$", re.S)
_PREAMBLE = re.compile(
    r"^(sure|certainly|of course|okay|ok|here(?:'s| is| are)|below is|the following)\b.*?:?\s*$",
    re.I,
)


def clean_output(text: str) -> str:
    """Strip reasoning blocks, wrapping code fences and chatty preambles."""
    text = _THINK.sub("", text).strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    lines = text.splitlines()
    while lines and (not lines[0].strip() or _PREAMBLE.match(lines[0].strip())):
        lines.pop(0)
    return "\n".join(lines).strip()


def truncate_lines(text: str, max_tokens: int) -> str:
    """Drop whole lines from the end until ``text`` (plus a note) fits."""
    if count_tokens(text) <= max_tokens:
        return text
    lines = text.splitlines()
    while lines and count_tokens("\n".join([*lines, TRUNCATION_NOTE])) > max_tokens:
        lines.pop()
    return "\n".join([*lines, TRUNCATION_NOTE]) if lines else TRUNCATION_NOTE


# ----- deterministic fallback -----------------------------------------------------------------


@dataclass
class _Line:
    priority: int  # lower is more important
    text: str


def _deterministic_sections(ctx: SessionContext) -> list[tuple[str, list[_Line]]]:
    blockers = ctx.decisions_tagged("blocker")
    architecture = ctx.decisions_tagged("architecture")
    bugs = ctx.decisions_tagged("bug")
    notes = ctx.decisions_tagged("note")
    todos = ctx.decisions_tagged("todo")
    uncommitted = sum(f.uncommitted for f in ctx.files)

    goal = [_Line(0, f"- Branch `{ctx.branch}` ({ctx.project})")]
    if ctx.commits:
        goal.append(_Line(0, f"- Latest commit: {ctx.commits[0].subject}"))

    done = [_Line(5, f"- {c.sha[:8]} {c.subject}") for c in ctx.commits]
    if uncommitted:
        done.append(_Line(3, f"- Uncommitted changes in {uncommitted} file(s)"))
    if not done:
        done.append(_Line(5, "- No commits yet in this session"))

    files = []
    for f in ctx.files:
        tag = f.change_type + (", uncommitted" if f.uncommitted else "")
        line = f"- {f.path} ({tag})"
        if f.symbols:
            line += f": {format_symbols(f.symbols, limit=6)}"
        files.append(_Line(4, line))
    if ctx.omitted_files:
        files.append(_Line(4, f"- ... and {ctx.omitted_files} more file(s)"))
    if not files:
        files.append(_Line(4, "- No file changes recorded"))

    decisions = [_Line(2, f"- {d.content}") for d in architecture]
    decisions += [_Line(7, f"- (note) {d.content}") for d in notes]
    if not decisions:
        decisions.append(_Line(7, "- None logged"))

    failing: list[_Line] = []
    run = ctx.test_run
    if run is not None:
        failing.append(_Line(3, f"- Last run: {describe_test_run(run)}"))
        for failure in run.failures:
            message = failure.get("message", "")
            failing.append(
                _Line(3, f"- {failure.get('test', '?')}" + (f": {message}" if message else ""))
            )
    failing += [_Line(3, f"- (bug) {d.content}") for d in bugs]
    if not failing:
        failing.append(_Line(3, "- No test runs recorded (use `cb run -- <test command>`)"))

    blocker_lines = [_Line(1, f"- {d.content}") for d in blockers] or [_Line(1, "- None")]
    next_steps = [_Line(5, f"- {d.content}") for d in todos] or [
        _Line(5, "- None logged (add with `cb log --tag todo`)")
    ]
    return [
        ("Goal / Branch", goal),
        ("Done", done),
        ("Files & symbols", files),
        ("Decisions", decisions),
        ("Failing tests", failing),
        ("Blockers", blocker_lines),
        ("Next steps", next_steps),
    ]


def _render_sections(sections: list[tuple[str, list[_Line]]]) -> str:
    parts = []
    for title, lines in sections:
        body = "\n".join(line.text for line in lines) if lines else "- (trimmed)"
        parts.append(f"## {title}\n{body}")
    return "\n\n".join(parts)


def deterministic_summary(ctx: SessionContext, max_tokens: int) -> str:
    """Template summary; lowest-priority lines are dropped until it fits."""
    sections = _deterministic_sections(ctx)
    text = _render_sections(sections)
    while count_tokens(text) > max_tokens:
        candidates_ = [
            (line.priority, si, li)
            for si, (_, lines) in enumerate(sections)
            for li, line in enumerate(lines)
        ]
        if not candidates_:
            break
        # Drop the least important line, latest in the document first.
        _, si, li = max(candidates_)
        del sections[si][1][li]
        text = _render_sections(sections)
    return text if count_tokens(text) <= max_tokens else truncate_lines(text, max_tokens)


# ----- LLM path --------------------------------------------------------------------------


def _user_prompt(context_text: str, target_tokens: int) -> str:
    return (
        "Session context (sanitized):\n"
        "<context>\n"
        f"{context_text}\n"
        "</context>\n\n"
        f"Write the handoff summary now, in at most {target_tokens} tokens. "
        "Output only the bulleted summary, with no preamble."
    )


def _stricter_prompt(context_text: str, target_tokens: int, previous_tokens: int) -> str:
    return (
        _user_prompt(context_text, target_tokens)
        + f"\n\nYour previous answer was about {previous_tokens} tokens, which is too long. "
        f"Rewrite it in at most {int(target_tokens * 0.75)} tokens: keep blockers, "
        "decisions, failing tests and key symbols; drop everything else."
    )


def _try_client(
    client: LLMClient,
    system: str,
    context_text: str,
    target_tokens: int,
    budget: int,
    attempts: list[Attempt],
) -> str | None:
    """One provider: ask, validate length, retry once if needed, then truncate."""
    name = client.provider
    try:
        text = clean_output(
            client.complete(
                system, _user_prompt(context_text, target_tokens), max_tokens=budget * 2
            )
        )
        if not text:
            attempts.append(Attempt(name, "empty response"))
            return None
        tokens = count_tokens(text)
        if tokens <= budget:
            attempts.append(Attempt(name, "ok"))
            return text
        log.info("compress: %s answer was %d tokens (budget %d); retrying", name, tokens, budget)
        retry = clean_output(
            client.complete(
                system, _stricter_prompt(context_text, target_tokens, tokens), max_tokens=budget
            )
        )
    except LLMClientError as exc:
        attempts.append(Attempt(name, str(exc)))
        log.info("compress: %s failed: %s", name, exc)
        return None
    except Exception as exc:  # A provider bug must never break the handoff.
        attempts.append(Attempt(name, f"unexpected error: {type(exc).__name__}"))
        log.warning("compress: %s raised", name, exc_info=True)
        return None
    if retry and count_tokens(retry) <= budget:
        attempts.append(Attempt(name, "retried"))
        return retry
    attempts.append(Attempt(name, "truncated"))
    return truncate_lines(retry or text, budget)


def compress(
    ctx: SessionContext,
    config: Config,
    *,
    summary_budget: int,
    no_llm: bool = False,
    clients: list[LLMClient] | None = None,
    notify: Callable[[str], None] | None = None,
) -> CompressionResult:
    """Summarize ``ctx`` within ``summary_budget`` tokens using the first working option."""
    attempts: list[Attempt] = []
    target = min(config.handoff.target_tokens, summary_budget)
    options = clients if clients is not None else candidates(config, no_llm=no_llm)
    if options:
        # Re-sanitize right before anything leaves the process (defense in depth).
        context_text = sanitize(ctx.render(config.handoff.max_input_tokens))
        system = sanitize(config.handoff.system_prompt)
        for client in options:
            if client.is_cloud and notify is not None:
                local_failed = [a.provider for a in attempts]
                reason = f" ({', '.join(local_failed)} unavailable)" if local_failed else ""
                notify(f"Sending sanitized context to {client.provider} ({client.model}){reason}.")
            summary = _try_client(client, system, context_text, target, summary_budget, attempts)
            if summary is not None:
                return CompressionResult(sanitize(summary), client.provider, attempts)
    summary = deterministic_summary(ctx, summary_budget)
    return CompressionResult(sanitize(summary), DETERMINISTIC, attempts)


# ----- payload ---------------------------------------------------------------------------------


def _payload_header(ctx: SessionContext, from_agent: str | None, to_agent: str | None) -> str:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        "# ContextBridge handoff\n"
        f"From: {from_agent or 'unknown'} -> To: {to_agent or 'unknown'} | "
        f"{timestamp} | Branch: {ctx.branch}"
    )


def _payload_footer(ctx: SessionContext) -> str:
    files = [f.path for f in ctx.files[:ACTIVE_FILES_IN_PAYLOAD]]
    listing = "\n".join(f"- {p}" for p in files) if files else "- (none recorded)"
    return f"## Active files\n{listing}\n\n{RESUME_LINE}"


def build_handoff(
    ctx: SessionContext,
    config: Config,
    *,
    from_agent: str | None = None,
    to_agent: str | None = None,
    no_llm: bool = False,
    clients: list[LLMClient] | None = None,
    notify: Callable[[str], None] | None = None,
) -> Handoff:
    """Compress ``ctx`` and lay out the payload, all within ``hard_cap_tokens``."""
    cap = config.handoff.hard_cap_tokens
    header = sanitize(_payload_header(ctx, from_agent, to_agent))
    footer = _payload_footer(ctx)
    overhead = count_tokens(f"{header}\n\n\n\n{footer}")
    if overhead > cap - MIN_SUMMARY_TOKENS:
        # Very small caps: shorten the file list rather than starving the summary.
        footer = RESUME_LINE
        overhead = count_tokens(f"{header}\n\n\n\n{footer}")
    summary_budget = max(cap - overhead, MIN_SUMMARY_TOKENS)
    result = compress(
        ctx, config, summary_budget=summary_budget, no_llm=no_llm, clients=clients, notify=notify
    )
    payload = f"{header}\n\n{result.summary}\n\n{footer}\n"
    if count_tokens(payload) > cap:  # Token boundaries can shift a little when joined.
        payload = truncate_lines(payload, cap) + "\n"
    return Handoff(
        payload=payload,
        method=result.method,
        raw_tokens=ctx.raw_tokens,
        compressed_tokens=count_tokens(payload),
        attempts=result.attempts,
    )
