"""Deterministic Chat Bridge parsing: pasted transcripts become task drafts.

No model is involved. The rules are fixed and ordered, so the same transcript always
produces the same tasks in the same order:

1. checkbox lines   ``- [ ] ship it`` / ``* [x] ship it``
2. marker lines     ``TODO: ...`` / ``ACTION: ...`` / ``NEXT: ...`` / ``FIXME: ...``
3. section bullets  bullets under a ``Next steps`` / ``Action items`` / ``To-do`` heading

Code fences are skipped, results are deduplicated, sanitized and capped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from contextbridge.core.sanitizer import sanitize

MAX_TASKS: Final = 100
MAX_TASK_CHARS: Final = 300

_CHECKBOX: Final = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[([ xX])\]\s*(?P<text>.+?)\s*$")
_MARKER: Final = re.compile(
    r"^\s*(?:[-*+]|\d+[.)]\s+)?(?P<tag>TODO|FIXME|ACTION ITEM|ACTION|NEXT STEPS?|"
    r"FOLLOW-?UPS?|TO-?DO|TASK)\s*[:\-\u2013]\s*(?P<text>\S.*?)\s*$",
    re.IGNORECASE,
)
_HEADING: Final = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>\S.*?)\s*$")
_LABEL: Final = re.compile(r"^\s*(?P<title>[^:!\n]{3,60}):\s*$")
_BULLET: Final = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<text>\S.*?)\s*$")
_SECTION: Final = re.compile(
    r"\b(to-?do|action items?|next steps?|tasks?|plans?|follow-?ups?|roadmap|"
    r"checklist|remaining work)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskDraft:
    """One task recovered from a transcript, with its source line for traceability."""

    content: str
    line: int
    kind: str  # "checkbox" | "marker" | "section"
    status: str  # "open" | "resolved"

    def to_dict(self) -> dict[str, object]:
        return {
            "content": self.content,
            "line": self.line,
            "kind": self.kind,
            "status": self.status,
        }


def _clean(text: str) -> str:
    return sanitize(text).strip()[:MAX_TASK_CHARS].rstrip(" .;,")


def _key(text: str) -> str:
    return " ".join(text.lower().split())


def parse_transcript_tasks(text: str) -> list[TaskDraft]:
    """Extract task drafts from a pasted chat transcript. Pure and deterministic."""
    if not text or not text.strip():
        return []

    drafts: list[TaskDraft] = []
    seen: set[str] = set()
    section: str | None = None
    in_fence = False

    def add(raw: str, line_no: int, kind: str, status: str = "open") -> None:
        if len(drafts) >= MAX_TASKS:
            return
        content = _clean(raw)
        if not content:
            return
        key = _key(content)
        if key in seen:
            return
        seen.add(key)
        drafts.append(TaskDraft(content=content, line=line_no, kind=kind, status=status))

    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        heading = _HEADING.match(line)
        if heading:
            title = heading.group("title").strip()
            section = title if _SECTION.search(title) else None
            continue

        checkbox = _CHECKBOX.match(line)
        if checkbox:
            checked = checkbox.group(1).lower() == "x"
            add(checkbox.group("text"), number, "checkbox", "resolved" if checked else "open")
            continue

        marker = _MARKER.match(line)
        if marker:
            add(marker.group("text"), number, "marker", "open")
            continue

        if not stripped:
            continue

        label = _LABEL.match(line)
        if label:
            title = label.group("title").strip()
            section = title if _SECTION.search(title) else None
            continue

        bullet = _BULLET.match(line)
        if bullet and section:
            add(bullet.group("text"), number, "section", "open")
            continue

        # A plain sentence that opens a section, e.g. "Next steps:" or "Action items".
        if _SECTION.search(stripped) and len(stripped) <= 60 and stripped.endswith(":"):
            section = stripped.rstrip(":").strip()

    return drafts


def drafts_to_tasks(drafts: list[TaskDraft]) -> list[dict[str, object]]:
    return [d.to_dict() for d in drafts]
