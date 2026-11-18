"""Offline conversation parsing and extractive, chronological chat handoffs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from contextbridge.core.sanitizer import sanitize
from contextbridge.synthesis.document import TITLES, render_document

MAX_CHAT_CHARS = 100_000
CHAT_TITLES = (*TITLES[:-1], "Files")
_ROLE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:\*\*)?"
    r"(User prompt|User|Human|You said|Assistant|Response|ChatGPT said|ChatGPT|"
    r"Gemini said|Gemini|AI)"
    r"(?:\*\*)?(?:\s*:\s*(?:\*\*)?\s*(.*)|\s*)$",
    re.I,
)
_COMPLETED = re.compile(
    r"\b(?:I(?: have|'ve)?|We(?: have|'ve)?)\s+(?:successfully\s+)?"
    r"(?:added|created|updated|implemented|completed|built|generated|fixed|finished|saved|included)\b"
    r"|\b(?:has|have) been (?:added|created|updated|completed|implemented)\b",
    re.I,
)
_FILE = re.compile(
    r"(?<![\w/])(?:[\w.-]+/)*[\w-]+\.(?:html?|md|txt|pdf|py|dart|jsx?|tsx?|json|css|yaml|yml|toml)\b",
    re.I,
)
_NOISE = re.compile(
    r"^\s*(?:\[?(?:tool(?: call| result| output)?|function call)\b|mcp__|"
    r"Thought for \d|Searching the web|Analyzing[. ]*$|Fetching[. ]*$)",
    re.I,
)


@dataclass(frozen=True)
class Turn:
    role: str
    text: str


@dataclass(frozen=True)
class ChatPacket:
    text: str
    source_chars: int
    omitted_chars: int
    warnings: tuple[str, ...] = ()


def clean_export(transcript: str) -> str:
    text = sanitize(transcript)
    text = re.sub(r"<tool\b[^>]*>.*?</tool>", "", text, flags=re.S | re.I)
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r".*?", "", text)
    return text


def parse_turns(transcript: str) -> list[Turn]:
    """Join wrapped prose within turns while excluding URLs, code and tool output."""
    text = clean_export(transcript)
    turns: list[Turn] = []
    role: str | None = None
    lines: list[str] = []
    fenced = False
    in_tool = False

    def finish() -> None:
        if role and lines:
            # Dedupe adjacent/repeated export lines, preserving their original position.
            prose = " ".join(dict.fromkeys(lines))
            if prose:
                turns.append(Turn(role, prose))

    for raw in text.splitlines():
        line = raw.strip()
        marker = _ROLE.fullmatch(line) or re.fullmatch(
            r"(You said|ChatGPT said|Gemini said)\s+(.+)", line, re.I
        )
        if marker and not fenced:
            finish()
            in_tool = False
            role = (
                "user"
                if marker[1].lower() in {"user prompt", "user", "human", "you said"}
                else "assistant"
            )
            lines = []
            if marker[2]:
                lines.append(marker[2].strip())
            continue
        if line.startswith("```"):
            fenced = not fenced
            continue
        if re.match(r"^(?:\[?tool\b|mcp__)", line, re.I):
            in_tool = True
        if fenced or in_tool or not line or _NOISE.match(line):
            continue
        if role is not None:
            # Remove list markers and headings, then keep the text to join wrapped sentences.
            line = re.sub(r"^(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)", "", line)
            if line and not line.endswith(":"):
                lines.append(line)
    finish()
    return turns


def chat_warnings(transcript: str) -> tuple[str, ...]:
    if len(transcript.split()) < 100 or not parse_turns(transcript):
        return (
            "This input does not look like a conversation. "
            "It has fewer than 100 words or no recognized turns.",
        )
    return ()


def sentences(text: str) -> list[str]:
    """Return complete prose units, retaining wrapped text and avoiding numeric list fragments."""
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip())
    result = []
    for piece in pieces:
        piece = " ".join(piece.split()).strip()
        if not piece or re.fullmatch(r"[\d\W]+", piece):
            continue
        if piece[-1] not in ".!?":
            piece += "."
        result.append(piece)
    return list(dict.fromkeys(result))


def _labeled(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"(?:^|(?<=[.!?])\s+)(Goal|Project|Done|Completed|Decision|Decisions|Decided|"
        r"Current state|Status|Problem|Blocker|Bug|Next steps|Next|Todo):\s*(.*?)(?=\s+"
        r"(?:Goal|Done|Decision|Blocker|Next|Todo):|$)",
        re.I,
    )
    return [(match[1].lower(), match[2].strip()) for match in pattern.finditer(text)]


def chat_sections(transcript: str, goal: str = "", pinned: str = "") -> dict[str, list[str]]:
    turns = parse_turns(transcript)
    cleaned = clean_export(transcript)
    users = [turn for turn in turns if turn.role == "user"]
    assistants = [turn for turn in turns if turn.role == "assistant"]
    sections: dict[str, list[str]] = {title: [] for title in CHAT_TITLES}
    requests = [
        turn
        for turn in users
        if len(turn.text.split()) >= 5
        or re.match(
            r"^(?:give|create|write|build|add|help|explain|make|show|fix|"
            r"implement|design|generate|please|goal:|next:|todo:)\b",
            turn.text,
            re.I,
        )
    ]
    substantial = requests[0] if requests else None
    if goal.strip():
        sections["Goal"] = sentences(sanitize(goal))[:1]
    elif substantial:
        sections["Goal"] = [" ".join(sentences(substantial.text))]
    if pinned.strip():
        sections["Decisions"] = sentences("User-pinned facts — " + sanitize(pinned))
    if users:
        sections["Current state"].extend(
            "Latest user request — " + line for line in sentences((requests or users)[-1].text)
        )
    if not turns:
        prose = " ".join(
            line.strip() for line in cleaned.splitlines() if line.strip() and not _NOISE.match(line)
        )
        sections["Current state"] = ["Imported text — " + line for line in sentences(prose)[:1]]
    if assistants:
        last = sentences(assistants[-1].text)
        summary = next((line for line in last if _COMPLETED.search(line)), last[0] if last else "")
        if summary:
            sections["Current state"].append("Latest assistant response — " + summary)
    if (
        users
        and assistants
        and sections["Current state"]
        and sections["Current state"][-1].startswith("Latest assistant response — ")
    ):
        latest_request = (requests or users)[-1]
        assistant_index = max(i for i, turn in enumerate(turns) if turn.role == "assistant")
        request_index = max(i for i, turn in enumerate(turns) if turn is latest_request)
        if assistant_index < request_index:
            assistant_line = sections["Current state"].pop()
            sections["Current state"].insert(0, assistant_line)
    # Explicitly labeled facts also work for short, unstructured imports, with a warning.
    evidence = turns or [Turn("unknown", cleaned)]
    mapping = {
        "goal": "Goal",
        "project": "Goal",
        "done": "Done",
        "completed": "Done",
        "decision": "Decisions",
        "decisions": "Decisions",
        "decided": "Decisions",
        "current state": "Current state",
        "status": "Current state",
        "problem": "Open problems",
        "blocker": "Open problems",
        "bug": "Open problems",
        "next": "Next steps",
        "next steps": "Next steps",
        "todo": "Next steps",
    }
    end_start = max((i for i, turn in enumerate(evidence) if turn.role == "assistant"), default=0)
    for index, turn in enumerate(evidence):
        if turn.role == "assistant":
            sections["Done"].extend(
                line for line in sentences(turn.text) if _COMPLETED.search(line)
            )
        for label, value in _labeled(turn.text.replace("\n", ". ")):
            title = mapping[label]
            if title == "Current state" and turns:
                continue
            if title == "Next steps" and index < end_start:
                continue
            if title == "Goal" and sections[title]:
                continue
            if title == "Done" and turn.role == "user":
                continue
            # Non-sentence labels receive a grammatical wrapper instead of a bare fragment.
            prefix = {
                "Goal": "The stated goal is ",
                "Decisions": "Recorded decision — ",
                "Next steps": "Explicit next step — ",
                "Done": "Reported completed work — ",
                "Open problems": "Reported problem — ",
            }.get(title, "Reported state — ")
            sections[title].extend(sentences(prefix + value))
    if turns:
        ending = sentences(turns[-1].text)
        for line in ending:
            if any(mapping[label] == "Next steps" for label, _ in _labeled(line)):
                continue
            explicit = bool(
                re.search(
                    r"\b(?:next(?: step)?|you can now|please (?:review|test|check)|todo)\b",
                    line,
                    re.I,
                )
            )
            if explicit or line.endswith("?"):
                sections["Next steps"].append(line)
        if turns[-1].role == "user" and turns[-1] in requests:
            sections["Next steps"].extend(ending)
    clean_text = " ".join(turn.text for turn in turns) if turns else cleaned
    sections["Files"] = [f"`{name}`" for name in dict.fromkeys(_FILE.findall(clean_text))]
    defaults = {
        "Goal": "No substantial user request was identified.",
        "Done": "No completed work was reported by the assistant.",
        "Decisions": "No decisions were explicitly stated.",
        "Current state": "No conversational current state was identified.",
        "Open problems": "No open problems were explicitly stated.",
        "Next steps": "No explicit next steps or final open questions were stated.",
        "Files": "No file names were mentioned.",
    }
    return {
        title: list(dict.fromkeys(sections[title])) or [defaults[title]] for title in CHAT_TITLES
    }


def condense_chat(transcript: str, *, goal: str = "", pinned: str = "", budget: int = 2000) -> str:
    if not transcript.strip():
        raise ValueError("Paste or import some chat text first.")
    if len(transcript) > MAX_CHAT_CHARS:
        raise ValueError("Choose a shorter conversation excerpt (at most 100,000 characters).")
    return render_document(
        chat_sections(transcript, goal, pinned),
        budget,
        header=(
            "# ContextBridge handoff\nQuoted conversation evidence. Review claims before sharing."
        ),
        titles=CHAT_TITLES,
        complete_items=True,
    )


def build_chat_packet(transcript: str, goal: str, pinned: str, budget: int = 6000) -> ChatPacket:
    if not transcript.strip() or len(transcript) > MAX_CHAT_CHARS:
        raise ValueError("Paste a conversation of at most 100,000 characters.")
    if not 2000 <= budget <= 12000:
        raise ValueError("Choose a size between 2,000 and 12,000 characters.")
    if len(goal) > 1000 or len(pinned) > 1000:
        raise ValueError("Keep the goal and essential facts under 1,000 characters each.")
    text = render_document(
        chat_sections(transcript, goal, pinned),
        budget,
        header="# ContextBridge handoff\nQuoted conversation evidence.",
        measure=len,
        titles=CHAT_TITLES,
        complete_items=True,
    )
    return ChatPacket(
        text, len(transcript), max(0, len(transcript) - len(text)), chat_warnings(transcript)
    )
