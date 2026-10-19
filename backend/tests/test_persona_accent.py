import numpy as np
import pytest
from PIL import Image, ImageDraw

from aethel.personas.accent import (DARK_L, LIGHT_L, MAX_C, accent_for, accent_from_rgb, dominant_colour,
                                    from_oklch, to_oklch)


def _hue(hex_colour: str) -> float:
    rgb = tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return to_oklch(rgb)[2]


def test_blue_background_wins_over_a_skin_face():
    img = Image.new("RGB", (200, 260), (40, 90, 200))
    ImageDraw.Draw(img).ellipse((40, 40, 160, 220), fill=(224, 172, 140))  # a large skin-tone face
    r, g, b = dominant_colour(img)
    assert b > r and b > g
    accent = accent_for(img)
    assert 230 < _hue(accent.light) < 290 and 230 < _hue(accent.dark) < 290


def test_grey_and_white_only_gives_none():
    img = Image.new("RGB", (100, 100), (250, 250, 250))
    ImageDraw.Draw(img).rectangle((0, 0, 50, 100), fill=(128, 128, 128))
    ImageDraw.Draw(img).rectangle((50, 0, 100, 30), fill=(10, 10, 10))
    assert dominant_colour(img) is None and accent_for(img) is None


@pytest.mark.parametrize("rgb", [(0, 0, 0), (255, 255, 255), (194, 85, 58), (40, 90, 200), (12, 200, 90),
                                 (255, 0, 255), (128, 128, 128)])
def test_oklch_round_trip(rgb):
    assert np.max(np.abs(np.array(from_oklch(*to_oklch(rgb))) - np.array(rgb))) <= 1


@pytest.mark.parametrize("rgb", [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (20, 0, 40), (250, 240, 255)])
def test_clamping_keeps_lightness_and_chroma_in_range(rgb):
    accent = accent_from_rgb(rgb)
    for hex_colour, (lo, hi) in ((accent.light, LIGHT_L), (accent.dark, DARK_L)):
        l, c, _ = to_oklch(tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)))
        assert lo - 0.01 <= l <= hi + 0.01 and c <= MAX_C + 0.01


def test_same_seed_same_answer():
    rng = np.random.default_rng(3)
    img = Image.fromarray(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8))
    assert dominant_colour(img, seed=1) == dominant_colour(img, seed=1)
