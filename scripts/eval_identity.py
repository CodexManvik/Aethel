"""E4: does a persona keep its face across photos (spec §10.3, §14)?

One persona with an anchor portrait (and, for the second condition, a character sheet), 50 everyday scenes, two
conditions: the anchor alone as reference, and anchor + sheet. Every photo is scored by the identity check (ArcFace
cosine to the anchor). Then 20 photos of other people (no references) give the "different face" scores, and τ_id is
calibrated: the lowest threshold that lets at most 5% of the different faces through. Reported per condition:
p10/p50/p90 of the scores, the share below τ (photos that would be redrawn), and how many same-persona photos pass
at the calibrated τ.

It SPENDS IMAGE CREDITS: 2 × scenes + distractors generations (120 by default) with the configured backend. Run it
only with the owner's go-ahead, with the app closed. The key comes from GEMINI_API_KEY / OPENROUTER_API_KEY.
Photos are drawn straight from the backend (no retries, nothing added to the persona's gallery).
Writes ~/.aethel/eval/identity.json. Put the calibrated value into ImageSettings.identity_threshold with a comment.
Usage: py -3.11 scripts/eval_identity.py --persona mira --yes [--scenes 50] [--distractors 20]"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

SCENES = [
    "at the beach at sunset", "in a café by the window", "walking in the rain at night under an umbrella",
    "hiking on a mountain trail", "reading on a sofa at home", "cooking pasta in a small kitchen",
    "at a farmers' market holding a bag of apples", "on a city rooftop at dusk", "in a library between shelves",
    "riding a bicycle along a canal", "at a concert in the crowd", "in a park in autumn leaves",
    "on a train looking out of the window", "at a desk with a laptop", "in a snowy street in winter",
    "by a lake in the morning mist", "at a picnic on the grass", "in an art gallery", "on a ferry in the wind",
    "at a bonfire on the beach at night", "in a bookshop", "trying on a hat in a shop", "in a botanical greenhouse",
    "on a balcony with plants", "in a train station", "jogging in the early morning", "at a birthday party",
    "in a record shop", "on a bridge over a river", "in a flower field", "at an old castle", "in a cosy pub",
    "watering plants at home", "on a beach boardwalk", "in a museum hall", "at a street food stall",
    "in a cinema foyer", "at the top of a lighthouse", "in a vineyard", "on a sailing boat", "in a bakery",
    "at a pottery wheel", "in a rainy bus shelter", "on a swing in a playground", "at an ice rink",
    "in a sunflower field", "at a window seat with tea", "in a night market with lanterns", "at a harbour",
    "on a forest path"]
DISTRACTOR = "a natural photo of a different adult, {scene}, looking at the camera"


# ---- metrics (unit-tested) ------------------------------------------------------------------------------------
def percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def calibrate(different: list[float], far: float = 0.05) -> float | None:
    """The lowest τ with at most `far` of the different-face scores at or above it."""
    if not different:
        return None
    s = sorted(different)
    allowed = int(far * len(s))  # how many may pass
    if allowed >= len(s):
        return s[0]
    return round(s[len(s) - 1 - allowed] + 1e-4, 4)


def summary(scores: list[float | None], tau: float | None, tau_now: float) -> dict:
    faced = [s for s in scores if s is not None]
    return {"n": len(scores), "no_face": len(scores) - len(faced),
            "p10": percentile(faced, 10), "p50": percentile(faced, 50), "p90": percentile(faced, 90),
            "below_tau_now": sum(s < tau_now for s in faced) / len(faced) if faced else None,
            "pass_at_calibrated": sum(s >= tau for s in faced) / len(faced) if faced and tau is not None else None}


# ---- the run --------------------------------------------------------------------------------------------------
async def run(args) -> dict:
    import httpx

    from aethel.images.backend import ImageRequest, pick_backend
    from aethel.images.compose import compose
    from aethel.images.gemini import GeminiImage
    from aethel.images.identity import IdentityCheck
    from aethel.images.openrouter import OpenRouterImage
    from aethel.images.service import to_jpeg
    from aethel.keys import KeyStore
    from aethel.paths import aethel_home
    from aethel.personas.store import PersonaStore
    from aethel.settings import SettingsService
    from aethel.store.db import Database
    from aethel.paths import db_path

    settings = SettingsService(Database(db_path())).get()
    keys = KeyStore()
    store = PersonaStore(aethel_home() / "personas")
    persona = store.get(args.persona)
    if persona is None or not persona.anchors:
        sys.exit(f"persona {args.persona!r} needs a face (an anchor) first")
    identity = IdentityCheck()
    if not identity.ready()[0]:
        sys.exit("the face check isn't installed: download it in Personas → Face first")
    folder = store.folder(persona.id)
    anchor = (folder / persona.anchors[0]).read_bytes()
    sheet = [(folder / s).read_bytes() for s in persona.sheet]
    anchor_vec = identity.embedding_of(anchor)
    if anchor_vec is None:
        sys.exit("no face found in the anchor portrait")
    async with httpx.AsyncClient() as http:
        backends = {"gemini": GeminiImage(key=lambda: keys.get("gemini"), model=lambda: settings.images.gemini_model, http=http),
                    "openrouter": OpenRouterImage(key=lambda: keys.get("openrouter"),
                                                  model=lambda: settings.images.openrouter_model, http=http)}
        backend = await pick_backend(settings.images.backends, backends, private=False)
        if backend is None:
            sys.exit("no cloud image backend: set GEMINI_API_KEY (or OPENROUTER_API_KEY)")
        scenes = SCENES[:args.scenes]
        conditions = {"anchor": [anchor]} | ({"anchor+sheet": [anchor, *sheet]} if sheet else {})
        out = {"persona": persona.id, "backend": backend.name, "tau_now": settings.images.identity_threshold,
               "scenes": scenes, "conditions": {}, "started": time.strftime("%Y-%m-%d %H:%M")}

        async def score(req) -> float | None:
            try:
                result = await backend.generate(req)
            except Exception as exc:
                print("  failed:", exc)
                return None
            return identity.compare(to_jpeg(result.data), anchor_vec)

        for name, refs in conditions.items():
            print(f"{name}: {len(scenes)} photos")
            out["conditions"][name] = [await score(ImageRequest(compose(persona, s), refs)) for s in scenes]
        print(f"distractors: {args.distractors} photos")
        out["different"] = [await score(ImageRequest(DISTRACTOR.format(scene=s)))
                            for s in SCENES[len(SCENES) - args.distractors:]]  # [-0:] would be all of them
    different = [s for s in out["different"] if s is not None]
    tau = calibrate(different)
    out["calibrated_tau"] = tau
    out["summary"] = {n: summary(v, tau, out["tau_now"]) for n, v in out["conditions"].items()}
    out["summary"]["different"] = summary(out["different"], tau, out["tau_now"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--persona", required=True)
    ap.add_argument("--scenes", type=int, default=50)
    ap.add_argument("--distractors", type=int, default=20)
    ap.add_argument("--yes", action="store_true", help="I know this spends image credits")
    args = ap.parse_args()
    if not args.yes:
        sys.exit(f"This makes {2 * args.scenes + args.distractors} images with a paid model. Re-run with --yes.")
    result = asyncio.run(run(args))
    path = Path(os.environ.get("AETHEL_HOME", str(Path.home() / ".aethel"))) / "eval" / "identity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2), f"\ncalibrated τ_id = {result['calibrated_tau']}\nwrote {path}")


if __name__ == "__main__":
    main()
