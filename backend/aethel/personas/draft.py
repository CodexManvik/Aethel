"""A first draft of a persona from the user's description. It's returned for editing, never saved by itself."""
import json
import re

from ..providers.base import ChatMessage, TextDelta
from .model import SECTIONS, Persona, PersonaVoice

DESCRIPTION_MAX = 2000
KEYS = {"personality": "Personality", "backstory": "Backstory", "speaking_style": "Speaking style",
        "boundaries": "Boundaries"}

DRAFT_SYSTEM = """You write character sheets for a companion app. From the user's description, write a persona.
Reply with ONE JSON object and nothing else:
{"name": "a first name", "image_style": "how photos of them look: film stock, light, lens, in under 15 words",
 "personality": "...", "backstory": "...", "speaking_style": "...", "boundaries": "..."}
- personality: second person ("You are ..."), 3-6 sentences: temperament, what they care about, quirks.
- backstory: 2-5 sentences of history that gives them things to talk about. Invent specifics.
- speaking_style: how they write messages: length, punctuation, slang, emoji, how often they ask questions.
- boundaries: what they won't do or talk about, and how they handle it, kindly.
Keep what the user asked for; fill gaps with choices that fit it. Plain text inside the strings, no Markdown."""


class DraftError(RuntimeError):
    pass


def parse_draft(raw: str) -> Persona:
    """The model's JSON (fenced or with chatter around it is fine) as an unsaved persona."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise DraftError("The draft didn't come back as JSON.")
    try:
        data = json.loads(m.group(0))
    except ValueError as exc:
        raise DraftError("The draft's JSON was malformed.") from exc
    if not isinstance(data, dict):
        raise DraftError("The draft wasn't an object.")
    text = {k: str(data.get(k) or "").strip() for k in (*KEYS, "name", "image_style")}
    if not text["personality"]:
        raise DraftError("The draft had no personality.")
    name = re.sub(r"\s+", " ", text["name"])[:60] or "New persona"
    return Persona(id="draft", name=name, image_style=text["image_style"][:200], voice=PersonaVoice(),
                   sections={KEYS[k]: text[k] for k in KEYS})


async def draft_persona(router, description: str) -> Persona:
    """One chat-role call, retried once if what comes back can't be parsed."""
    convo = [ChatMessage("system", DRAFT_SYSTEM), ChatMessage("user", description.strip()[:DESCRIPTION_MAX])]
    last: DraftError | None = None
    for _ in range(2):
        parts = []
        async for ev in router.stream("chat", convo, temperature=0.8, purpose="persona_draft"):
            if isinstance(ev, TextDelta):
                parts.append(ev.text)
        raw = "".join(parts)
        try:
            return parse_draft(raw)
        except DraftError as exc:
            last = exc
            convo = [*convo, ChatMessage("assistant", raw),
                     ChatMessage("user", "That wasn't a single valid JSON object. Reply with only the JSON object.")]
    raise last


assert set(KEYS.values()) == set(SECTIONS)
