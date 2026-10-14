import random

import pytest

from aethel.voice.chunker import SentenceChunker


def chunks(text, **kw):
    c = SentenceChunker(**kw)
    return c.feed(text) + c.flush()


def streamed(text, seed, **kw):
    rng = random.Random(seed)
    c, out, i = SentenceChunker(**kw), [], 0
    while i < len(text):
        n = rng.choice([1, 1, 2, 3, 5, 8, 21])
        out += c.feed(text[i:i + n])
        i += n
    return out + c.flush()


SAMPLE = ("Hello there, Dr. Smith! It costs 3.5 pounds, e.g. for tea, etc. and coffee. Mr. J. K. Rowling agreed... "
          "Really? Yes!? Okay. Let's go.\n- one item here\n- another item here\n# A heading line\nThe end. "
          "(See above.) \"Quoted words here.\" Then " + "a very long clause that keeps going, " * 9 + "and finally stops.")


@pytest.mark.parametrize("seed", range(20))
def test_streaming_in_any_deltas_gives_the_same_sentences(seed):
    assert streamed(SAMPLE, seed) == chunks(SAMPLE)


@pytest.mark.parametrize("seed", range(20))
def test_same_with_a_small_limit(seed):
    assert streamed(SAMPLE, seed, max_chars=60, min_chars=8) == chunks(SAMPLE, max_chars=60, min_chars=8)


def test_splits_at_sentence_ends():
    assert chunks("First sentence here. Second one there! Third one? Yes.") == [
        "First sentence here.", "Second one there!", "Third one? Yes."]


def test_not_at_decimals_abbreviations_or_initials():
    assert chunks("It costs 3.5 pounds today and tomorrow.") == ["It costs 3.5 pounds today and tomorrow."]
    assert chunks("Mr. Jones and Mrs. Smith met Dr. Who, e.g. at lunch, i.e. noon, vs. dinner, etc. Then left.") == [
        "Mr. Jones and Mrs. Smith met Dr. Who, e.g. at lunch, i.e. noon, vs. dinner, etc. Then left."]
    assert chunks("Written by J. K. Rowling in Edinburgh. Next.") == ["Written by J. K. Rowling in Edinburgh.", "Next."]


def test_a_short_sentence_merges_with_the_next():
    assert chunks("Okay. Let's go and see the library.") == ["Okay. Let's go and see the library."]
    assert chunks("Okay. Let's go and see. Then we leave for home.") == [
        "Okay. Let's go and see.", "Then we leave for home."]


def test_newlines_end_sentences_so_list_items_and_headings_are_apart():
    assert chunks("# Opening times\n- Monday to Friday nine to five\n- Saturday ten to two\n") == [
        "# Opening times", "- Monday to Friday nine to five", "- Saturday ten to two"]


def test_ellipsis_and_mixed_punctuation_are_one_boundary():
    assert chunks("Well, I suppose so... Maybe not though?! Fine then.") == ["Well, I suppose so...", "Maybe not though?!", "Fine then."]


def test_closing_quotes_and_brackets_stay_with_their_sentence():
    assert chunks('She said "go home now." Then (quietly) left the room.') == [
        'She said "go home now."', "Then (quietly) left the room."]


def test_long_sentence_splits_at_the_last_comma_then_space():
    text = "alpha beta gamma delta, epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho"
    assert chunks(text, max_chars=50) == ["alpha beta gamma delta,", "epsilon zeta eta theta iota kappa lambda mu nu xi", "omicron pi rho"]
    no_commas = "word " * 30
    parts = chunks(no_commas, max_chars=40)
    assert all(len(p) <= 40 for p in parts) and " ".join(parts).split() == no_commas.split()
    assert all(len(p) <= 40 for p in chunks("x" * 100, max_chars=40))  # no break at all: cut hard


def test_nothing_is_said_until_the_sentence_is_complete_and_flush_returns_the_tail():
    c = SentenceChunker()
    assert c.feed("The library opens") == [] and c.feed(" at nine.") == []  # the full stop could still be "9.5"
    assert c.feed(" It closes") == ["The library opens at nine."]
    assert c.flush() == ["It closes"]
    assert c.flush() == []


def test_flush_emits_a_short_leftover_and_empty_input_gives_nothing():
    c = SentenceChunker()
    assert c.feed("Okay.") == [] and c.flush() == ["Okay."]
    assert chunks("") == [] and chunks("   \n\n  ") == []
