"""Check the utility model extracts facts as well as the chat model (token-efficiency spec §5.2).

Runs every case in backend/aethel/eval/extract_fixtures.json through the real extraction prompt on the
first model of the utility role and the first model of the chat role, with your keys, and scores exact
agreement with the expected operations. Spends a few thousand tokens per model: run it yourself.
Writes ~/.aethel/eval/extract.json. Usage: py -3.11 scripts/eval_extract.py"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DATA = ROOT / "backend" / "aethel" / "eval" / "extract_fixtures.json"


def matches(expected: dict, got: dict) -> bool:
    if not isinstance(got, dict) or got.get("op") != expected["op"]:
        return False
    if expected["op"] in ("update", "delete") and got.get("id") != expected["id"]:
        return False
    text = str(got.get("text") or "").lower()
    return all(word.lower() in text for word in expected.get("contains", []))


def score(case: dict, ops: list | None) -> bool:
    """Right when every expected op is matched by a different returned op and nothing else was returned."""
    ops = [o for o in (ops or []) if isinstance(o, dict)]
    if len(ops) != len(case["expect"]):
        return False
    remaining = list(ops)
    for expected in case["expect"]:
        hit = next((o for o in remaining if matches(expected, o)), None)
        if hit is None:
            return False
        remaining.remove(hit)
    return True


async def run_model(entry, settings, keys, cases) -> dict:
    import httpx

    from aethel.memory.extract import extraction_messages, parse_ops
    from aethel.providers.base import TextDelta
    from aethel.providers.catalog import api_key_for
    from aethel.providers.router import LOCAL_API_KEY, make_provider_factory

    key = LOCAL_API_KEY if entry.provider == "local" else api_key_for(entry.provider, keys, settings)[0]
    if key is None:
        return {"model": f"{entry.provider}:{entry.model}", "error": "no key"}
    async with httpx.AsyncClient(timeout=60) as http:
        provider = make_provider_factory(http)(entry, key, settings)
        results = []
        for case in cases:
            text = []
            async for ev in provider.stream(extraction_messages(case["known"], case["previous"], case["message"]),
                                            temperature=0.1, max_tokens=300):
                if isinstance(ev, TextDelta):
                    text.append(ev.text)
            ops = parse_ops("".join(text))
            results.append({"message": case["message"], "ok": score(case, ops), "ops": ops})
    right = sum(r["ok"] for r in results)
    return {"model": f"{entry.provider}:{entry.model}", "right": right, "n": len(results), "results": results}


async def main() -> None:
    from aethel.keys import KeyStore
    from aethel.paths import aethel_home, db_path
    from aethel.settings import SettingsService
    from aethel.store.db import Database

    cases = json.loads(DATA.read_text(encoding="utf-8"))["cases"]
    db = Database(db_path())
    settings = SettingsService(db).get()
    keys = KeyStore()
    out = {}
    for role in ("utility", "chat"):
        entries = settings.roles.get(role) or []
        if not entries:
            print(f"{role}: no model set")
            continue
        out[role] = await run_model(entries[0], settings, keys, cases)
        r = out[role]
        print(f"{role} ({r['model']}): " + (r.get("error") or f"{r['right']}/{r['n']} right"))
        for item in r.get("results", []):
            if not item["ok"]:
                print(f"   wrong: {item['message']!r} -> {item['ops']}")
    if "utility" in out and "chat" in out and "right" in out["utility"] and "right" in out["chat"]:
        keep = out["utility"]["right"] >= out["chat"]["right"] - 1
        out["verdict"] = "utility is good enough" if keep else "utility is worse: leave extraction on chat"
        print(out["verdict"])
    path = aethel_home() / "eval" / "extract.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
