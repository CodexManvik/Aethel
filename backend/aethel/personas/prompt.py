"""The system prompt a persona gives chat and calls. It's identical every turn, so it leads the prompt (and is
cached by the provider); the time note still goes last."""
import re

from .model import Persona

BACKSTORY_MAX = 600
STYLE_MAX = 300


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def persona_system_prompt(p: Persona, style: str | None = None) -> str:
    """The name line (unless the personality already opens with it), the personality, then the other sections in
    the file's order under their headings (the backstory shortened), then the imported style directive. The
    built-in Aethel has only its personality, so its prompt is exactly the text it has always had."""
    personality = p.section("Personality")
    parts = []
    if not re.match(rf"\s*you are {re.escape(p.name)}\b", personality, re.I):
        parts.append(f"You are {p.name}.")
    if personality:
        parts.append(personality)
    for name, text in p.sections.items():
        if name == "Personality" or not text:
            continue
        if name == "Backstory":
            text = _clip(text, BACKSTORY_MAX)
        parts.append(f"{name}:\n{text}")
    if style:
        parts.append(f"How you write (learned from real messages):\n{style.strip()}")
    return "\n\n".join(parts)


def speaking_hint(p: Persona) -> str | None:
    """For a task's closing summary: who is speaking and how. None for the built-in, whose tools stay unchanged."""
    if p.built_in:
        return None
    style = p.section("Speaking style")
    return f"Speak as {p.name}" + (f": {_clip(style, STYLE_MAX)}" if style else ".")
