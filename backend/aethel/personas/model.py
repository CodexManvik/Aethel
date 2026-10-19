"""persona.md: YAML frontmatter (who the persona is to the app) and ## sections (who it is to the model)."""
import re
from collections.abc import Collection
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from ..voice.tts import VoiceBlend

ID_RE = r"^[a-z0-9][a-z0-9-]{0,40}$"
SECTIONS = ("Personality", "Backstory", "Speaking style", "Boundaries")
MAX_SECTIONS = 12
HEX_RE = r"^#[0-9a-fA-F]{6}$"
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_HEADING = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)
SECTION_NAME_RE = r"[^\r\n#][^\r\n]{0,39}"
# A line in a section that starts with "##" would read back as a new section, so it's written with one more leading
# backslash, and one is taken off when it's read: "## Rules" ↔ "\## Rules", "\## x" ↔ "\\## x".
_ESCAPED = re.compile(r"^\\(\\*##)", re.M)
_TO_ESCAPE = re.compile(r"^(\\*##)", re.M)


class PersonaVoice(BaseModel):
    blend: dict[str, float] = Field(default_factory=lambda: {"af_heart": 1.0})
    speed: float = 1.0

    def to_blend(self, known: Collection[str] | None = None) -> VoiceBlend:
        """Normalised and checked; raises ValueError for an unknown voice (when the known ones are given)."""
        return VoiceBlend.parse({**self.blend, "speed": self.speed}, known)


class Accent(BaseModel):
    light: str = Field(pattern=HEX_RE)
    dark: str = Field(pattern=HEX_RE)
    source: Literal["anchor", "manual"] = "anchor"


class Persona(BaseModel):
    id: str = Field(pattern=ID_RE)
    name: str = Field(min_length=1, max_length=60)
    built_in: bool = False
    voice: PersonaVoice = Field(default_factory=PersonaVoice)
    accent: Accent | None = None
    image_style: str = ""
    anchors: list[str] = []
    sheet: list[str] = []
    image_frequency: Literal["never", "on_request", "sometimes"] = "on_request"
    tone_engine: bool = True
    created: str | None = None
    sections: dict[str, str] = Field(default_factory=lambda: {s: "" for s in SECTIONS})
    extra: dict[str, Any] = {}  # frontmatter keys this version doesn't know, kept so a newer file survives a save

    @field_validator("sections")
    @classmethod
    def _standard_sections(cls, v: dict[str, str]) -> dict[str, str]:
        """The four standard sections always exist (empty if absent); any others keep their place. A name is one
        short line that can't itself look like a heading."""
        out = {k.strip(): (t or "").strip() for k, t in v.items() if k.strip()}
        for name in out:
            if not re.fullmatch(SECTION_NAME_RE, name):
                raise ValueError(f"not a section name: {name[:40]!r}")
        for s in SECTIONS:
            out.setdefault(s, "")
        if len(out) > MAX_SECTIONS:
            raise ValueError(f"a persona has at most {MAX_SECTIONS} sections")
        return out

    def section(self, name: str) -> str:
        return self.sections.get(name, "")


FIELDS = [f for f in Persona.model_fields if f not in ("sections", "extra")]


def parse_persona(text: str, folder_id: str, known_voices: Collection[str] | None = None) -> Persona:
    """Tolerant: missing keys take their defaults, missing sections are empty, unknown keys are kept in `extra`.
    The folder name is the id (a file can't move itself). Raises ValueError for a bad id or voice blend."""
    if not re.fullmatch(ID_RE, folder_id):
        raise ValueError(f"not a persona id: {folder_id!r}")
    meta: dict[str, Any] = {}
    body = text
    m = _FRONTMATTER.match(text)
    if m:
        loaded = yaml.safe_load(m.group(1)) or {}
        if not isinstance(loaded, dict):
            raise ValueError("the frontmatter isn't a mapping")
        meta, body = loaded, text[m.end():]
    known = {k: meta[k] for k in FIELDS if k in meta and meta[k] is not None}
    extra = {k: v for k, v in meta.items() if k not in FIELDS}
    known["id"] = folder_id
    known.setdefault("name", folder_id.replace("-", " ").title())
    if "created" in known:
        known["created"] = str(known["created"])  # YAML reads 2026-10-10 as a date
    persona = Persona(**known, sections=_sections(body), extra=extra)
    persona.voice.to_blend(known_voices)
    return persona


def _sections(body: str) -> dict[str, str]:
    heads = list(_HEADING.finditer(body))
    out: dict[str, str] = {}
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        out[h.group(1)] = _ESCAPED.sub(r"\1", body[h.end():end].strip())
    return out


def _escape(text: str) -> str:
    return _TO_ESCAPE.sub(lambda m: "\\" + m.group(1), text)


def render_persona(p: Persona) -> str:
    """The file; parse_persona(render_persona(p), p.id) == p."""
    meta = p.model_dump(include=set(FIELDS) - {"id"}, mode="json")
    meta = {"id": p.id, **meta, **p.extra}
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=None, width=1000)
    body = "\n\n".join(f"## {name}\n{_escape(text)}".rstrip() for name, text in p.sections.items())
    return f"---\n{front}---\n{body}\n"
