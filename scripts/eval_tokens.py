"""Does leaving stale screens out of a task's context cost anything? (token-efficiency spec §7)

Runs 6 short desktop tasks through Aethel's real task engine and desktop control, with your model keys and
settings, with superseded-state masking OFF and ON. The arms are interleaved (the order flips each round,
so rate limits and warm-up don't favour one), and the ON arm masks as eagerly as possible (batch 1), so it's
the strict version of the question. Learned skills are off for the run, so both arms do the full LLM loop.

Success is the task finishing AND an independent check of its result where one exists (the file is there,
it says 7006652, …), not just the model saying it's done.

Safety: close the Aethel app first (it checks), and don't use the PC while it runs. Approvals are answered
"allow once" only for the windows that task is about (Notepad for the haiku, …) and files under the eval
folder; anything else, and anything irreversible, is denied and printed. Your settings are restored at the end, and on
the next start if a run was killed. It spends your tokens: about 24 tasks' worth.
Writes ~/.aethel/eval/tokens.json. Usage: py -3.11 scripts/eval_tokens.py [--reps 2]
On your own model: --base-url http://127.0.0.1:8080/v1 --model model.gguf --context 128000 --timeout 900"""
import argparse
import asyncio
import json
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

WORK = Path.home() / "Documents" / "Aethel" / "eval-tokens"
TASK_TIMEOUT_S = 300
ARMS = {"masking off": {"mask_superseded": False}, "masking on": {"mask_superseded": True, "mask_batch": 1}}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# The windows each task may act in. A desktop approval for any other window (say the user clicked into
# WhatsApp mid-run and it took focus) is denied: the script must never type or press keys elsewhere.
# Store apps (Calculator, Settings) report their host window, "applicationframehost", not their own name.
WINDOWS = {"haiku": ("notepad", "save as"),
           "calc": ("calculator", "applicationframehost", "notepad", "save as"),
           "folder": ("explorer", "eval-tokens"), "url": ("edge", "example"),
           "edit": ("notepad", "notes"), "display": ("settings", "applicationframehost")}


def decide(task: str, tool: str, summary: str, tier: str) -> str:
    """Answer an approval the way the user would for this test task: 'allow_once' or 'deny'."""
    if tier == "irreversible":
        return "deny"
    if tool.startswith("fs_"):
        return "allow_once" if str(WORK).lower() in summary.lower() else "deny"
    if tool.startswith("win_"):
        where = summary.rsplit(" in ", 1)[-1].lower() if " in " in summary else ""
        return "allow_once" if any(w in where for w in WINDOWS.get(task, ())) else "deny"
    return "deny"


def tasks() -> list[tuple[str, str, object]]:
    """(name, goal, check): check(workdir) -> bool, or None when only the task's own verdict is available."""
    w = str(WORK)
    return [
        ("haiku", f"Open Notepad, write a haiku about rain, and save it as {w}\\haiku.txt",
         lambda d: len(_read(d / "haiku.txt").split()) >= 5),
        ("calc", f"Use Calculator to work out 1234 times 5678, then write just the result into {w}\\result.txt",
         lambda d: "7006652" in _read(d / "result.txt").replace(",", "")),
        ("folder", f"In File Explorer, create a new folder called Receipts inside {w}",
         lambda d: (d / "Receipts").is_dir()),
        ("url", "Open https://example.com in Microsoft Edge", None),
        ("edit", f"Open {w}\\notes.txt in Notepad, add a line saying 'buy milk' at the end, and save it",
         lambda d: "buy milk" in _read(d / "notes.txt") and "eggs" in _read(d / "notes.txt")),
        ("display", "Open the Display page in Windows Settings", None),
    ]


def reset_workspace() -> None:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "notes.txt").write_text("shopping\n- eggs\n", encoding="utf-8")


def summarise(runs: list[dict]) -> dict:
    ok = [r for r in runs if r["success"]]
    mean = lambda xs: round(statistics.mean(xs), 1) if xs else None  # noqa: E731
    return {"runs": len(runs), "success_rate": round(len(ok) / len(runs), 3) if runs else None,
            "mean_prompt_tokens": mean([r["usage"]["prompt"] for r in runs]),
            "mean_completion_tokens": mean([r["usage"]["completion"] for r in runs]),
            "mean_calls": mean([r["usage"]["calls"] for r in runs]),
            "mean_seconds": mean([r["seconds"] for r in runs]),
            "estimated": any(r["usage"]["estimated"] for r in runs)}


def schedule(names: list[str], reps: int) -> list[tuple[str, str]]:
    """(task, arm) pairs: every task in every round under both arms, the order flipping each round."""
    order = list(ARMS)
    out = []
    for rep in range(reps):
        arms = order if rep % 2 == 0 else order[::-1]
        for name in names:
            out += [(name, arm) for arm in arms]
    return out


def app_is_running() -> bool:
    import httpx
    try:
        httpx.get("http://127.0.0.1:8765/api/health", timeout=1)
        return True
    except httpx.HTTPError:
        return False


async def run_one(svc, name: str, goal: str, check) -> dict:
    from aethel.runtime.store import TERMINAL_STATES
    reset_workspace()
    conv = svc.conversations.create(title=f"eval-tokens: {name}")
    t0 = time.monotonic()
    task_id = await svc.engine.start(conversation_id=conv.id, goal=goal)
    while time.monotonic() - t0 < TASK_TIMEOUT_S:
        for a in svc.approvals.pending_for(task_id):
            decision = decide(name, a.tool, a.summary, a.tier)
            print(f"      {decision}: {a.tool} {a.summary}")
            await svc.approvals.resolve(a.approval_id, decision)
        if svc.tasks.get(task_id).state in TERMINAL_STATES:
            break
        await asyncio.sleep(0.5)
    else:
        await svc.engine.cancel(task_id)
    await svc.engine.wait_idle()
    task = svc.tasks.get(task_id)
    checked = check(WORK) if check is not None else None
    return {"task": name, "state": task.state, "checked": checked,
            "success": task.state == "done" and checked is not False,
            "error": task.error, "seconds": round(time.monotonic() - t0, 1),
            "usage": svc.usage.task_totals(task_id)}


async def main(args) -> None:
    from aethel.paths import aethel_home
    from aethel.services import build_services

    if app_is_running():
        print("The Aethel app is running. Close it first: this drives the desktop itself.")
        return
    marker = aethel_home() / "eval" / "tokens-settings-backup.json"
    svc = build_services()
    if marker.exists():  # an earlier run was killed before it restored your settings
        svc.settings.update(json.loads(marker.read_text(encoding="utf-8")))
        marker.unlink()
        print("Restored your settings from an interrupted run.")
    s = svc.settings.get()
    saved = {"use_learned_skills": s.use_learned_skills, "token_saving": s.token_saving.model_dump(),
             "custom_base_url": s.custom_base_url, "roles": {k: [e.model_dump() for e in v] for k, v in s.roles.items()}}
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(saved), encoding="utf-8")
    if args.base_url:  # every role on one endpoint of your own, for this run only
        entry = {"provider": "custom", "model": args.model, "context_size": args.context}
        svc.settings.update({"custom_base_url": args.base_url.rstrip("/"),
                             "roles": {role: [entry] for role in ("chat", "agent", "utility", "vision")}})
        print(f"All roles on {args.base_url} ({args.model}, {args.context} tokens of context) for this run.")
    global TASK_TIMEOUT_S
    TASK_TIMEOUT_S = args.timeout
    reps = args.reps
    svc.mcp.start(svc.mcp_servers)
    result = {"reps": reps, "arms": {arm: {"runs": []} for arm in ARMS}}
    try:
        if not await svc.mcp.wait_ready("windows", 180):
            print("Desktop control didn't start (see ~/.aethel/logs/mcp-windows.log).")
            return
        goals = {name: (goal, check) for name, goal, check in tasks()}
        for name, arm in schedule(list(goals), reps):
            svc.settings.update({"use_learned_skills": False, "token_saving": ARMS[arm]})
            print(f"[{arm}] {name}")
            r = await run_one(svc, name, *goals[name])
            print(f"      {'ok' if r['success'] else 'FAILED'} ({r['state']}, check {r['checked']}) in {r['seconds']} s, "
                  f"{r['usage']['prompt']} in / {r['usage']['completion']} out, {r['usage']['calls']} calls")
            result["arms"][arm]["runs"].append(r)
    finally:
        svc.settings.update(saved)
        marker.unlink(missing_ok=True)
        shutil.rmtree(WORK, ignore_errors=True)
        await svc.engine.shutdown()
        await svc.mcp.stop()
        svc.close()
    for arm, data in result["arms"].items():
        data["summary"] = summarise(data["runs"])
        print(arm, data["summary"])
    out = aethel_home() / "eval" / "tokens.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=300, help="seconds per task (raise for a slow local model)")
    parser.add_argument("--base-url", help="run every role on this OpenAI-compatible endpoint (e.g. llama-server)")
    parser.add_argument("--model", default="model.gguf")
    parser.add_argument("--context", type=int, default=131072, help="that model's context size in tokens")
    asyncio.run(main(parser.parse_args()))
