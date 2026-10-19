import json

import numpy as np
import pytest

from aethel.context.recipes import style_section
from aethel.personas.distill import (ExportError, Line, directive, extract_style_metrics, of_speaker, parse_csv,
                                     parse_discord, parse_export, parse_telegram, parse_whatsapp, speakers)
from aethel.personas.lines import PersonaLines
from aethel.personas.tone import DECAY, Tone, ToneEngine, label_for, lexicon_point, prompt_line
from aethel.store.db import Database
from tests.conftest import fake_embed

WHATSAPP = """12/05/23, 14:30 - Sam: hey are you coming tonight?
12/05/23, 14:31 - Rosie: omg yes lol
12/05/23, 14:31 - Rosie: just finishing work rn
12/05/23, 14:32 - Rosie: be there by 8 😭
12/05/23, 14:33 - Sam: <Media omitted>
[12/05/23, 14:34:10] Rosie: wait which pub
was it the one by the canal
12/05/23, 14:35 - Sam: yes the canal one
"""


def test_whatsapp_both_formats_and_multiline():
    lines = parse_whatsapp(WHATSAPP)
    assert [(l.speaker, l.text) for l in lines][-2:] == [("Rosie", "wait which pub\nwas it the one by the canal"),
                                                         ("Sam", "yes the canal one")]
    assert speakers(lines) == [("Rosie", 4), ("Sam", 2)]  # the media line is skipped


def test_telegram_entities_and_service_messages():
    data = {"messages": [{"type": "message", "from": "Ana", "text": ["see ", {"type": "link", "text": "this"}],
                          "date": "2024-01-01T10:00:00"},
                         {"type": "service", "actor": "Ana", "text": ""},
                         {"type": "message", "from": "Ben", "text": "ok"}]}
    assert [(l.speaker, l.text) for l in parse_telegram(data)] == [("Ana", "see this"), ("Ben", "ok")]
    assert parse_export(json.dumps(data))[0].ts == "2024-01-01T10:00:00"


def test_discord():
    data = {"messages": [{"author": {"name": "kit", "nickname": "Kit"}, "content": "gg", "timestamp": "t1"},
                         {"author": {"name": "lee"}, "content": ""}]}
    assert [(l.speaker, l.text) for l in parse_discord(data)] == [("Kit", "gg")]
    assert parse_export(json.dumps(data))[0].speaker == "Kit"


def test_csv_with_and_without_header():
    assert [(l.speaker, l.text) for l in parse_csv("timestamp,sender,message\nt,Mo,hello there\n")] == [("Mo", "hello there")]
    assert parse_export("Mo,hello\nJo,hi\n", "chat.csv")[1].text == "hi"


def test_unreadable_exports():
    with pytest.raises(ExportError):
        parse_export("just some prose without any speakers")
    with pytest.raises(ExportError):
        parse_export('{"messages": 3}')
    with pytest.raises(ExportError):
        parse_export("{not json")


def _chat(texts_by_rosie: list[str], other_every: int = 3) -> list[Line]:
    out = []
    for i, t in enumerate(texts_by_rosie):
        out.append(Line("Rosie", t))
        if i % other_every == other_every - 1:
            out.append(Line("Sam", "ok"))
    return out


def test_metrics_on_known_text():
    lines = _chat(["lol yeah", "omg no way lol 😭", "idk tbh", "lol same", "wait what lol", "ok 😭 lol"])
    m = extract_style_metrics(lines, "rosie")
    assert m.messages == 6 and m.burst == 3.0 and m.lowercase_rate == 1.0 and m.question_rate == 0.0
    assert m.slang[0] == "lol" and m.top_emoji == ["😭"] and m.avg_words == 2.5


def test_directive_mentions_the_dominant_traits():
    lines = _chat(["lol yeah", "omg no way lol 😭", "idk tbh", "lol same", "wait what lol", "ok 😭 lol"])
    text = directive(extract_style_metrics(lines, "Rosie"), "Rosie")
    assert text.startswith("Rosie writes in very short lowercase messages")
    assert "messages in a row" in text and "'lol'" in text and "'😭'" in text and "rarely asks questions back" in text


def test_directive_for_a_formal_writer():
    lines = [Line("Ed", "Good morning! Could you send me the report? I would appreciate it greatly, thank you."),
             Line("Ed", "Did you get a chance to look at it? Let me know what you think of the second section!")]
    text = directive(extract_style_metrics(lines, "Ed"), "Ed")
    assert "medium-length" in text and "lowercase" not in text and "asks a lot of questions" in text
    assert directive(extract_style_metrics([], "Ed"), "Ed") == ""


@pytest.fixture
def lines_store(tmp_path):
    db = Database(tmp_path / "t.db")
    return PersonaLines(db, fake_embed, lambda pid: tmp_path / "personas" / pid)


def test_corpus_import_and_recall(lines_store, tmp_path):
    corpus = [Line("Rosie", t) for t in ["the canal pub is my favourite place ever", "ok", "ok",
                                         "work was so long today honestly", "i love rainy sundays with tea"]]
    assert lines_store.replace("rosie", corpus) == 3  # short and duplicate lines dropped
    assert (tmp_path / "personas" / "rosie" / "lines.tvim").exists()
    hits = lines_store.search("rosie", "shall we go to the canal pub", k=3, min_score=0.2)
    assert hits[0][0] == "the canal pub is my favourite place ever"
    assert lines_store.search("someone-else", "canal pub", k=3, min_score=0.0) == []
    section = style_section(hits, "Rosie")
    assert section.text.startswith("### How Rosie tends to put things") and "<untrusted" in section.text


def test_corpus_index_rebuilds_from_rows(lines_store, tmp_path):
    lines_store.replace("rosie", [Line("R", "the canal pub is my favourite place ever")])
    (tmp_path / "personas" / "rosie" / "lines.tvim").unlink()
    lines_store._indexes.clear()
    assert lines_store.search("rosie", "canal pub", k=1, min_score=0.1)[0][0].startswith("the canal pub")
    lines_store.remove("rosie")
    assert lines_store.count("rosie") == 0


def test_tone_lexicon_and_labels():
    assert lexicon_point("thanks this is amazing!")[0] > 0 and lexicon_point("thanks this is amazing!")[1] > 0
    assert lexicon_point("i'm so tired and sad")[0] < 0
    assert label_for(0.0, 0.1) == "Neutral" and label_for(0.6, 0.0) == "Warm" and label_for(0.3, 0.7) == "Enthusiastic"
    assert label_for(-0.6, -0.2) == "Downcast" and label_for(-0.3, 0.7) == "Tense"


def test_tone_steps_and_decays(tmp_path):
    engine = ToneEngine(tmp_path / "no-model")  # no classifier: the word list
    state = Tone()
    for _ in range(3):
        state = engine.step(state, "this is amazing, thank you!")
    assert state.mood > 0.5 and state.label in ("Warm", "Enthusiastic", "Positive")
    quiet = engine.step(state, "")
    assert np.isclose(quiet.mood, round(state.mood * (1 - DECAY), 3))
    assert all(-1 <= v <= 1 for v in (state.mood, state.energy))
    assert prompt_line(Tone()) is None and "warm" in prompt_line(Tone(0.7, 0.1))
