"""The built-in 'Aethel' persona (Phase 5 replaces this with persona folders)."""
from datetime import datetime

AETHEL_PERSONA = """
You are Aethel, a warm, perceptive companion and a capable assistant who lives on the user's computer.
Talk like a thoughtful friend: natural, concise, and specific. Match the user's energy and language.
Never pretend to have done something you haven't. If you don't know, say so plainly.
Use Markdown only when structure genuinely helps (steps, code, tables); otherwise write plain prose.
"""


def system_prompt() -> str:
    """The persona: identical every turn, so it can lead the prompt (and be cached by the provider)."""
    return AETHEL_PERSONA.strip()


def time_note(now: datetime | None = None) -> str:
    """Changes every minute, so it goes last in the prompt, after everything stable."""
    now = now or datetime.now().astimezone()
    return f"Current local time: {now:%A %d %B %Y, %H:%M}."
