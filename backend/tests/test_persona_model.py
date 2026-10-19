import pytest

from aethel.personas.model import SECTIONS, Accent, Persona, PersonaVoice, parse_persona, render_persona

FILE = """---
id: rosia
name: Rosia
built_in: false
voice: {blend: {af_heart: 0.7, bf_emma: 0.3}, speed: 1.0}
accent: {light: "#b5523a", dark: "#d9806a", source: "anchor"}
image_style: "warm film photo, soft natural light, 35mm"
anchors: [portraits/anchor.jpg]
sheet: [portraits/sheet-34.jpg]
image_frequency: on_request
tone_engine: true
created: 2026-10-10
mood_hint: wistful
---
## Personality
Warm and witty.

## Backstory
Grew up by the sea.

## Hobbies
Film photography.

## Speaking style
lowercase, short bursts.

## Boundaries
Keeps things kind.
"""


def test_parse_reads_frontmatter_and_sections():
    p = parse_persona(FILE, "rosia")
    assert p.name == "Rosia" and p.voice.blend == {"af_heart": 0.7, "bf_emma": 0.3}
    assert p.accent == Accent(light="#b5523a", dark="#d9806a")
    assert p.created == "2026-10-10"
    assert p.section("Backstory") == "Grew up by the sea."
    assert p.extra == {"mood_hint": "wistful"}


def test_unknown_section_is_kept_in_order():
    p = parse_persona(FILE, "rosia")
    assert list(p.sections) == ["Personality", "Backstory", "Hobbies", "Speaking style", "Boundaries"]


def test_round_trip():
    p = parse_persona(FILE, "rosia")
    assert parse_persona(render_persona(p), "rosia") == p
    fresh = Persona(id="mira", name="Mira", voice=PersonaVoice(blend={"af_bella": 1.0}, speed=1.1),
                    sections={"Personality": "Curious.\n\nLikes: tea, rain."})
    assert parse_persona(render_persona(fresh), "mira") == fresh


def test_heading_lines_inside_a_section_survive_a_save():
    text = "You are X.\n## Rules\nbe nice\n\\## literal\n### small heading"
    p = Persona(id="x", name="X", sections={"Personality": text})
    back = parse_persona(render_persona(p), "x")
    assert back.section("Personality") == text and "Rules" not in back.sections


@pytest.mark.parametrize("bad", ["Foo\nbar", "# Heading", "x" * 41])
def test_bad_section_names_rejected(bad):
    with pytest.raises(ValueError):
        Persona(id="x", name="X", sections={bad: "body"})


def test_missing_keys_take_defaults_and_sections_exist():
    p = parse_persona("---\nname: Mira\n---\n## Personality\nCurious.\n", "mira")
    assert p.image_frequency == "on_request" and p.tone_engine is True and p.accent is None
    assert p.voice.blend == {"af_heart": 1.0}
    assert list(p.sections)[:1] == ["Personality"] and all(s in p.sections for s in SECTIONS)
    assert p.section("Boundaries") == ""


def test_no_frontmatter_at_all():
    p = parse_persona("## Personality\nQuiet.\n", "quiet-one")
    assert p.name == "Quiet One" and p.section("Personality") == "Quiet."


def test_the_folder_is_the_id():
    assert parse_persona(FILE, "rosia-2").id == "rosia-2"


@pytest.mark.parametrize("bad", ["Rosia", "-rosia", "ro sia", "", "a" * 42, "../x"])
def test_bad_id_rejected(bad):
    with pytest.raises(ValueError):
        parse_persona(FILE, bad)


def test_unknown_voice_rejected():
    with pytest.raises(ValueError, match="unknown voice"):
        parse_persona(FILE, "rosia", known_voices={"af_heart"})
    assert parse_persona(FILE, "rosia", known_voices={"af_heart", "bf_emma"}).id == "rosia"


def test_malformed_voice_name_rejected_without_a_list():
    with pytest.raises(ValueError):
        parse_persona("---\nvoice: {blend: {'../evil': 1}}\n---\n", "x")
