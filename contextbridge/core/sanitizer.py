"""Secret redaction.

Every piece of text must pass through :func:`sanitize` before it touches SQLite,
a vector store, a log file, or an LLM prompt. Matches are replaced with
``[REDACTED:<type>]``; matched values are never returned or logged.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import PurePath

REDACTED_FMT = "[REDACTED:{}]"


def _tag(kind: str) -> str:
    return REDACTED_FMT.format(kind)


@dataclass(frozen=True)
class _Rule:
    kind: str
    pattern: re.Pattern[str]
    # Builds the replacement from the match; defaults to replacing the whole match.
    replace: Callable[[re.Match[str]], str] | None = None


def _keep_group(group: int, kind: str) -> Callable[[re.Match[str]], str]:
    """Keep the text of ``group`` (e.g. a key name or scheme) and redact the rest."""

    def _repl(m: re.Match[str]) -> str:
        return f"{m.group(group)}{_tag(kind)}"

    return _repl


# Order matters: more specific patterns run first so that, e.g., an Anthropic key
# is labelled `anthropic_key` rather than `openai_key`.
_RULES: tuple[_Rule, ...] = (
    _Rule(
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
            r"[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)"
        ),
    ),
    _Rule(
        "basic_auth",
        re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/:@]+:[^\s/@]+@"),
        lambda m: f"{m.group(1)}{_tag('basic_auth')}@",
    ),
    _Rule(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}"),
    ),
    _Rule(
        "bearer_token",
        re.compile(r"\b([Bb]earer\s+)[A-Za-z0-9\-._~+/]{8,}=*"),
        _keep_group(1, "bearer_token"),
    ),
    _Rule("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    _Rule("stripe_key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    _Rule("openai_key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}")),
    _Rule(
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    ),
    _Rule("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}")),
    _Rule("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]+")),
    _Rule("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b")),
    _Rule(
        "aws_secret_key",
        re.compile(
            r"(?i)(aws_?secret_?(?:access_?)?key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"
        ),
        _keep_group(1, "aws_secret_key"),
    ),
    _Rule(
        # .env-style assignment whose (upper-case) key name looks secret.
        # Values containing brackets/parens are treated as code, not literals.
        "env_secret",
        re.compile(
            r"(?m)^(\s*(?:export\s+)?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|"
            r"PRIVATE)[A-Z0-9_]*\s*=\s*)(?!\[REDACTED)"
            r"(\"[^\"\n]+\"|'[^'\n]+'|[^\s\"'()\[\]{}]+)(?=\s|$)"
        ),
        _keep_group(1, "env_secret"),
    ),
)

# Last-resort high-entropy detector.
_ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_\-]{33,}")
_HEX_OR_UUID = re.compile(r"[0-9a-fA-F\-]+")
ENTROPY_THRESHOLD = 4.5


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def _looks_like_secret(token: str) -> bool:
    if "[REDACTED" in token:
        return False
    # Git SHAs, hashes and UUIDs are hex(-with-dashes): never redact them.
    if _HEX_OR_UUID.fullmatch(token):
        return False
    # Identifiers / prose (no digits) and paths are not secrets.
    if not any(ch.isdigit() for ch in token) or not any(ch.isalpha() for ch in token):
        return False
    if token.count("/") >= 3 or token.count("_") >= 4 or token.count("-") >= 4:
        return False
    return shannon_entropy(token) >= ENTROPY_THRESHOLD


@dataclass
class SanitizeResult:
    text: str
    redactions: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.redactions.values())


def sanitize_with_report(text: str) -> SanitizeResult:
    """Redact secrets and report how many of each type were removed (never the values)."""
    counts: Counter[str] = Counter()
    for rule in _RULES:
        replace = rule.replace

        def _sub(m: re.Match[str], kind: str = rule.kind, rep: object = replace) -> str:
            counts[kind] += 1
            return rep(m) if callable(rep) else _tag(kind)

        text = rule.pattern.sub(_sub, text)

    def _entropy_sub(m: re.Match[str]) -> str:
        token = m.group(0)
        if _looks_like_secret(token):
            counts["high_entropy"] += 1
            return _tag("high_entropy")
        return token

    text = _ENTROPY_CANDIDATE.sub(_entropy_sub, text)
    return SanitizeResult(text=text, redactions=dict(counts))


def sanitize(text: str) -> str:
    """Return ``text`` with secrets replaced by ``[REDACTED:<type>]`` markers."""
    return sanitize_with_report(text).text


SENSITIVE_FILE_GLOBS: tuple[str, ...] = (".env", ".env.*", "*.pem", "*.key", "id_rsa*")


def is_sensitive_file(path: str | PurePath) -> bool:
    """True if the file must be skipped entirely (``.env*``, ``*.pem``, ``*.key``, ``id_rsa*``)."""
    name = PurePath(path).name
    return any(fnmatch(name, pattern) for pattern in SENSITIVE_FILE_GLOBS)
