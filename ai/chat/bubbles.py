"""Structured bubble response parsing and fallback rules."""

import json
import re

MAX_BUBBLES = 6
MAX_BUBBLE_CHARS = 3000
MAX_CONVERSATIONAL_LINE_CHARS = 80
MAX_CONVERSATIONAL_BLOCK_CHARS = 240

_MARKDOWN_LINE = re.compile(
    r"^\s*(?:```|#{1,6}\s|>|[-*+]\s|\d+[.)、]\s*|\|)"
)


def _split_conversational_lines(value: str) -> list[str]:
    """Split short IM-style lines without breaking rich/structured content."""
    text = str(value or "").strip()
    if "\n" not in text or "```" in text:
        return [text] if text else []

    # Blank lines are often emitted between short IM messages. They are visual
    # separators, not a reason to collapse the whole reply into one bubble.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if (
        len(lines) < 2
        or len(lines) > MAX_BUBBLES
        or any(_MARKDOWN_LINE.match(line) for line in lines)
        or any(len(line) > MAX_CONVERSATIONAL_LINE_CHARS for line in lines)
        or sum(len(line) for line in lines) > MAX_CONVERSATIONAL_BLOCK_CHARS
    ):
        return [text]
    return lines


def parse_bubble_response(raw_content: str) -> list[str]:
    """Parse bubbles and recover short conversational lines as separate items."""
    raw = str(raw_content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [item[:MAX_BUBBLE_CHARS] for item in _split_conversational_lines(raw)][:MAX_BUBBLES]

    values = payload.get("bubbles", []) if isinstance(payload, dict) else []
    if not isinstance(values, list):
        return [raw[:MAX_BUBBLE_CHARS]] if raw else []
    bubbles = []
    for value in values[:MAX_BUBBLES]:
        for item in _split_conversational_lines(str(value)):
            if item:
                bubbles.append(item[:MAX_BUBBLE_CHARS])
            if len(bubbles) >= MAX_BUBBLES:
                break
        if len(bubbles) >= MAX_BUBBLES:
            break
    return bubbles or ([raw[:MAX_BUBBLE_CHARS]] if raw else [])
