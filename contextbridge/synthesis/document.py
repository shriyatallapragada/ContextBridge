"""A shared section format with space reserved for essential context."""

from __future__ import annotations

from collections.abc import Callable

from contextbridge.core.sanitizer import sanitize
from contextbridge.errors import UserError
from contextbridge.synthesis.token_counter import count_tokens, truncate_to_tokens

TITLES = (
    "Goal",
    "Done",
    "Decisions",
    "Current state",
    "Open problems",
    "Next steps",
    "Files to read first",
)
ESSENTIAL = ("Goal", "Done", "Decisions", "Next steps")


def render_document(
    sections: dict[str, list[str]],
    budget: int,
    *,
    header: str = "# ContextBridge handoff",
    measure: Callable[[str], int] = count_tokens,
    titles: tuple[str, ...] = TITLES,
    complete_items: bool = False,
) -> str:
    """Keep every section populated, allocate essential context, then add file details."""
    clean = {
        title: [
            sanitize(" ".join(line.split())).replace("(trimmed)", "…")
            for line in sections.get(title, [])
            if line.strip()
        ]
        or ["Nothing logged."]
        for title in titles
    }
    bodies = {title: [clean[title][0]] for title in titles}

    def render() -> str:
        return (
            header
            + "\n\n"
            + "\n\n".join(
                f"## {title}\n" + "\n".join("- " + line for line in bodies[title])
                for title in titles
            )
            + "\n"
        )

    if complete_items:
        notices = {title: f"{title} details exceed this budget." for title in titles}
        while measure(render()) > budget:
            candidates = [title for title in titles if bodies[title][0] != notices[title]]
            if not candidates:
                raise UserError("Increase the budget to include complete conversation statements.")
            title = max(candidates, key=lambda key: len(bodies[key][0]))
            bodies[title][0] = notices[title]
    else:
        # Reserve a useful first item in each section before allocating optional detail.
        minimum = {
            title: min(count_tokens(clean[title][0]), 48 if title in ESSENTIAL else 20)
            for title in titles
        }
        for title in titles:
            bodies[title][0] = truncate_to_tokens(clean[title][0], minimum[title], marker="…")
        while measure(render()) > budget:
            title = max(titles, key=lambda key: len(bodies[key][0]))
            text = bodies[title][0]
            if len(text) <= 2:
                raise UserError(
                    "The budget is too small to include all handoff sections. "
                    "Use at least 120 tokens."
                )
            bodies[title][0] = text[: max(1, len(text) * 3 // 4 - 1)].rstrip("…") + "…"
    # Extend the reserved items first. Files receive the remaining space last.
    order = (
        *(title for title in ESSENTIAL if title in titles),
        *(title for title in titles if title not in ESSENTIAL),
    )
    for title in order:
        original = bodies[title][0]
        bodies[title][0] = clean[title][0]
        if measure(render()) > budget:
            bodies[title][0] = original
        for line in clean[title][1:]:
            bodies[title].append(line)
            if measure(render()) > budget:
                bodies[title].pop()
                break
    return render()
