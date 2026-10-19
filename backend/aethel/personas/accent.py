"""A persona's accent colour, taken from its anchor portrait: the dominant colour that isn't skin, white, black or
grey, moved into a lightness and chroma range that reads well on paper (light) and on charcoal (dark)."""
import numpy as np
from PIL import Image

from .model import Accent

SAMPLE_PX = 96
MIN_CHROMA = 0.03
MIN_SHARE = 0.05
LIGHT_L, DARK_L, MAX_C = (0.55, 0.68), (0.68, 0.78), 0.13


def _to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_srgb(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.abs(c) ** (1 / 2.4) * np.sign(c) - 0.055)


_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])


def _oklab(rgb: np.ndarray) -> np.ndarray:
    """(..., 3) sRGB in 0–255 → (..., 3) OKLab."""
    lms = _to_linear(np.asarray(rgb, dtype=np.float64) / 255.0) @ _M1.T
    return np.cbrt(lms) @ _M2.T


def _from_oklab(lab: np.ndarray) -> np.ndarray:
    """OKLab → gamma-encoded sRGB in 0–1, unclamped (out of gamut shows as < 0 or > 1)."""
    lms = (np.asarray(lab, dtype=np.float64) @ np.linalg.inv(_M2).T) ** 3
    return _to_srgb(lms @ np.linalg.inv(_M1).T)


def to_oklch(rgb) -> tuple[float, float, float]:
    l, a, b = _oklab(np.asarray(rgb, dtype=np.float64))
    return float(l), float(np.hypot(a, b)), float(np.degrees(np.arctan2(b, a)) % 360)


def _lab(l: float, c: float, h: float) -> np.ndarray:
    rad = np.radians(h)
    return np.array([l, c * np.cos(rad), c * np.sin(rad)])


def from_oklch(l: float, c: float, h: float) -> tuple[int, int, int]:
    """sRGB; a colour outside sRGB keeps its lightness and hue and loses chroma until it fits."""
    rgb = _from_oklab(_lab(l, c, h))
    if np.any(rgb < -1e-6) or np.any(rgb > 1 + 1e-6):
        lo, hi = 0.0, c
        for _ in range(30):
            mid = (lo + hi) / 2
            test = _from_oklab(_lab(l, mid, h))
            if np.any(test < -1e-6) or np.any(test > 1 + 1e-6):
                hi = mid
            else:
                lo = mid
        rgb = _from_oklab(_lab(l, lo, h))
    out = np.clip(np.round(rgb * 255), 0, 255).astype(int)
    return int(out[0]), int(out[1]), int(out[2])


def _skin(px: np.ndarray) -> np.ndarray:
    """The classic YCrCb skin box (Cr 133–173, Cb 77–127)."""
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cr = (r - y) * 0.713 + 128
    cb = (b - y) * 0.564 + 128
    return (cr >= 133) & (cr <= 173) & (cb >= 77) & (cb <= 127)


def _kmeans(x: np.ndarray, k: int, seed: int, iters: int = 20) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    centres = x[rng.choice(len(x), size=min(k, len(x)), replace=False)]
    for _ in range(iters):
        labels = np.argmin(((x[:, None, :] - centres[None]) ** 2).sum(-1), axis=1)
        moved = np.array([x[labels == i].mean(0) if np.any(labels == i) else centres[i] for i in range(len(centres))])
        if np.allclose(moved, centres):
            break
        centres = moved
    return centres, labels


def dominant_colour(image: Image.Image, k: int = 5, seed: int = 0) -> tuple[int, int, int] | None:
    """The centre of the largest colour cluster once skin, near-white, near-black and greys are dropped; None if
    under 5% of the picture is left (a grey photo has no accent to give)."""
    small = image.convert("RGB")
    small.thumbnail((SAMPLE_PX, SAMPLE_PX))
    px = np.asarray(small, dtype=np.float64).reshape(-1, 3)
    lab = _oklab(px)
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    keep = (chroma >= MIN_CHROMA) & (lab[:, 0] > 0.15) & (lab[:, 0] < 0.95) & ~_skin(px)
    if keep.sum() < MIN_SHARE * len(px):
        return None
    centres, labels = _kmeans(lab[keep], k, seed)  # clustered in OKLab, where distance follows perception
    best = centres[np.bincount(labels, minlength=len(centres)).argmax()]
    rgb = np.clip(np.round(_from_oklab(best) * 255), 0, 255).astype(int)
    return int(rgb[0]), int(rgb[1]), int(rgb[2])


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def accent_from_rgb(rgb: tuple[int, int, int], source: str = "anchor") -> Accent:
    l, c, h = to_oklch(rgb)
    c = min(c, MAX_C)
    light = from_oklch(min(max(l, LIGHT_L[0]), LIGHT_L[1]), c, h)
    dark = from_oklch(min(max(l, DARK_L[0]), DARK_L[1]), c, h)
    return Accent(light=_hex(light), dark=_hex(dark), source=source)


def accent_for(image: Image.Image) -> Accent | None:
    rgb = dominant_colour(image)
    return accent_from_rgb(rgb) if rgb is not None else None
