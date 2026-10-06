"""How many tokens does each tool's schema cost? (token-efficiency spec §6, roadmap §1)

Starts Aethel's tools (the same ones an agent call carries), measures what the provider receives for each
one, and prints the cost per tool and per group. It spends no model time and no tokens: with --base-url it
asks your llama-server's /tokenize for exact counts, otherwise it estimates at 4 characters per token.
It doesn't touch the desktop (it only lists the tools). Close nothing; your settings aren't changed.

Writes ~/.aethel/eval/tool_schemas.json.
Usage: py -3.11 scripts/tool_schema_report.py [--base-url http://127.0.0.1:8080/v1] [--compact]"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

SERVERS = ("windows", "office", "files")


def spec_json(spec) -> str:
    """Exactly what the provider receives for this one tool."""
    from aethel.providers.openai_compat import _wire_tools
    return json.dumps(_wire_tools([spec])[0], ensure_ascii=False, separators=(",", ":"))


def exact_counts(texts: list[str], base_url: str | None) -> list[int] | None:
    """The server's own token counts (llama-server's /tokenize), or None when it isn't there to ask."""
    if not base_url:
        return None
    import httpx
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        with httpx.Client(timeout=10) as client:
            out = []
            for text in texts:
                r = client.post(f"{root}/tokenize", json={"content": text})
                r.raise_for_status()
                out.append(len(r.json()["tokens"]))
            return out
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def count_tokens(texts: list[str], base_url: str | None) -> list[int]:
    """Exact counts when `base_url` answers, otherwise len // 4."""
    return exact_counts(texts, base_url) or [len(t) // 4 for t in texts]


def report(specs, counts: list[int], groups: dict[str, str] | None = None) -> list[dict]:
    total = sum(counts) or 1
    rows = [{"tool": s.name, "group": (groups or {}).get(s.name, "-"), "tokens": n, "share": round(n / total, 3)}
            for s, n in zip(specs, counts)]
    return sorted(rows, key=lambda r: r["tokens"], reverse=True)


def by_group(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["group"]] = out.get(r["group"], 0) + r["tokens"]
    return dict(sorted(out.items(), key=lambda kv: kv[1], reverse=True))


async def main(args) -> None:
    from aethel.paths import aethel_home
    from aethel.services import build_services

    svc = build_services()
    svc.mcp.start(svc.mcp_servers)
    try:
        for server in SERVERS:
            if not await svc.mcp.wait_ready(server, 180):
                print(f"The {server} tools didn't start (see ~/.aethel/logs/mcp-{server}.log): they're left out.")
        kwargs = {"compact": True} if args.compact else {}
        specs = svc.registry.specs(**kwargs)
        groups = {n: (getattr(svc.registry.get(n), "toolgroup", None) or svc.registry.get(n).group or "-")
                  for n in svc.registry.names()}
        texts = [spec_json(s) for s in specs]
        exact = exact_counts(texts, args.base_url)
        counts = exact or [len(t) // 4 for t in texts]
    finally:
        await svc.mcp.stop()
        svc.close()
    rows = report(specs, counts, groups)
    total = sum(counts)
    note = "exact, from the model server's tokenizer" if exact else "estimated at 4 characters per token"
    print(f"{len(rows)} tools, {total} tokens ({note}){', compact' if args.compact else ''}\n")
    print(f"{'tool':<28}{'group':<16}{'tokens':>8}{'share':>8}")
    for r in rows:
        print(f"{r['tool']:<28}{r['group']:<16}{r['tokens']:>8}{r['share']:>8.1%}")
    print("\nper group:")
    for g, n in by_group(rows).items():
        print(f"  {g:<16}{n:>8}  {n / (total or 1):.1%}")
    out = aethel_home() / "eval" / "tool_schemas.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"total": total, "exact": bool(exact), "compact": args.compact, "tools": rows,
                               "groups": by_group(rows)}, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", help="a llama-server endpoint, for exact token counts (read-only)")
    parser.add_argument("--compact", action="store_true", help="measure the compacted schemas (needs Task 2)")
    asyncio.run(main(parser.parse_args()))
