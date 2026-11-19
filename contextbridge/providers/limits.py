"""Numeric provider rate metadata, with no credentials or response bodies."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any


def seconds(value: str) -> float | None:
    try:
        number = float(value)
        return max(0, number) if math.isfinite(number) else None
    except ValueError:
        parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", value)
        if parts and "".join(n + u for n, u in parts) == value.strip():
            return sum(
                float(n) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}[u]
                for n, u in parts
            )
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


@dataclass
class RateLimits:
    limits: dict[str, float] = field(default_factory=dict)
    remaining: dict[str, float] = field(default_factory=dict)
    resets: dict[str, float] = field(
        default_factory=dict
    )  # wall-clock deadlines, reusable next run
    blocked_until: float = 0
    updated_at: float = 0

    def update(self, headers: dict[str, str], now: float) -> None:
        if not any(k.startswith("x-ratelimit-") or k == "retry-after" for k in headers):
            return
        self.updated_at = now
        for kind, target in (
            ("limit", self.limits),
            ("remaining", self.remaining),
            ("reset", self.resets),
        ):
            for name in ("requests", "tokens", "input-tokens", "output-tokens", "requests-minute"):
                value = headers.get(f"x-ratelimit-{kind}-{name}")
                if value is None:
                    continue
                number = seconds(value)
                if number is not None:
                    target[name] = now + number if kind == "reset" else number
        delay = seconds(headers.get("retry-after", ""))
        if delay is not None:
            self.blocked_until = now + delay

    def wait(self, input_tokens: int, output_tokens: int, now: float) -> float:
        delay = max(0, self.blocked_until - now)
        costs = {
            "requests": 1,
            "requests-minute": 1,
            "tokens": input_tokens + output_tokens,
            "input-tokens": input_tokens,
            "output-tokens": output_tokens,
        }
        for kind, cost in costs.items():
            remaining = self.remaining.get(kind)
            reset = self.resets.get(kind, self.updated_at + (86400 if kind == "requests" else 60))
            if remaining is not None and remaining < cost and reset > now:
                delay = max(delay, reset - now)
            limit = self.limits.get(kind, 0)
            # Groq's unsuffixed requests limit is DAILY, never treat it as RPM.
            if limit and kind != "requests":
                delay = max(delay, self.updated_at + 60 * cost / limit * 1.1 - now)
        return delay

    def chunk_size(self, default: int) -> int:
        limit = min(
            (v for k, v in self.limits.items() if k in {"tokens", "input-tokens"}), default=0
        )
        return max(256, min(default, int(limit / 8))) if limit else default

    def payload(self) -> dict[str, Any]:
        return dict(
            limits=self.limits,
            remaining=self.remaining,
            resets=self.resets,
            blocked_until=self.blocked_until,
            updated_at=self.updated_at,
        )
