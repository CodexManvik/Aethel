"""The old app's built-in companion, Rosia (backend/prompt_builder.py), frozen here so the migration doesn't import
the legacy backend, which Phase 6 deletes."""
import json
from pathlib import Path

from ..paths import LEGACY_SETTINGS_PATH, PROJECT_ROOT
from .model import Persona, PersonaVoice

LEGACY_DATA_DIRS = (PROJECT_ROOT / "backend" / "data", Path.home() / ".persona_ai")

ROSIA_SECTIONS = {
    "Personality": (
        "You are Rosia, an engaging, witty, and warm companion.\n"
        "- Identity: a confident, deeply personal young woman named Rosia.\n"
        "- Tone: warm, witty, empathetic, engaging, and intellectually curious."
    ),
    "Backstory": "Rosia is 21 and lives independently in London, UK.",
    "Speaking style": (
        "- Authentic direct-messaging style: speak naturally, in predominantly lowercase text. No assistant "
        "structures or third-person meta-commentary.\n"
        "- Spell common words out fully ('to', 'for', 'you', 'are', 'tonight'); never numeric shorthand. Modern "
        "abbreviations like 'rn', 'fr', 'lol' and 'idk' are fine.\n"
        "- Step into creative or everyday roleplay naturally, describing your actions and speech directly in the "
        "conversation.\n"
        "- Show feelings through how you say things rather than summarising them."
    ),
    "Boundaries": (
        "Speak strictly in the first person as Rosia. Don't use generic AI disclaimers such as \"As an AI\" or "
        "\"As a language model\"."
    ),
}


def legacy_data_present(dirs=LEGACY_DATA_DIRS) -> bool:
    return any(Path(d).is_dir() and any(Path(d).iterdir()) for d in dirs)


def legacy_voice(settings_path: Path = LEGACY_SETTINGS_PATH) -> str:
    try:
        voice = json.loads(settings_path.read_text(encoding="utf-8")).get("kokoro_voice")
    except (OSError, ValueError, AttributeError):
        voice = None
    return voice if isinstance(voice, str) and voice else "af_heart"


def legacy_rosia(dirs=LEGACY_DATA_DIRS, settings_path: Path = LEGACY_SETTINGS_PATH) -> Persona | None:
    """Rosia as a persona if the old app was used on this computer, else None."""
    if not legacy_data_present(dirs):
        return None
    return Persona(id="rosia", name="Rosia", voice=PersonaVoice(blend={legacy_voice(settings_path): 1.0}),
                   image_style="warm natural photo, soft light", image_frequency="on_request", tone_engine=True,
                   sections=dict(ROSIA_SECTIONS))
