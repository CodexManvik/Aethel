"""Does leaving stale screens out of a task's context cost anything? (token-efficiency spec §7)

Runs 6 short desktop tasks twice each with superseded-state masking OFF, then twice each with it ON,
through Aethel's real task engine and desktop control, with your model keys and settings. Learned skills
are switched off for the run (both arms do the full LLM loop) and your settings are restored afterwards.
Reports success rate, LLM calls, prompt/completion tokens and wall time per arm.

Close the Aethel app first: this drives your desktop itself, and approvals are answered "allow once"
automatically (each one is printed). It spends your tokens: roughly 24 tasks' worth.
Writes ~/.aethel/eval/tokens.json. Usage: py -3.11 scripts/eval_tokens.py [--reps 2]"""
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


def tasks() -> list[tuple[str, str]]:
    w = str(WORK)
    return [
        ("haiku", f"Open Notepad, write a haiku about rain, and save it as {w}\\haiku.txt"),
        ("calc", f"Use Calculator to work out 1234 times 5678, then write just the result into {w}\\result.txt"),
        ("folder", f"In File Explorer, create a new folder called Receipts inside {w}"),
        ("url", "Open https://example.com in Microsoft Edge"),
        ("edit", f"Open {w}\\notes.txt in Notepad, add a line saying 'buy milk' at the end, and save it"),
        ("display", "Open the Display page in Windows Settings"),
    ]


def reset_workspace() -> None:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "notes.txt").write_text("shopping\n- eggs\n", encoding="utf-8")


def summarise(runs: list[dict]) -> dict:
    ok = [r for r in runs if r["state"] == "done"]
    mean = lambda xs: round(statistics.mean(xs), 1) if xs else None  # noqa: E731
    return {"runs": len(runs), "success_rate": round(len(ok) / len(runs), 3) if runs else None,
            "mean_prompt_tokens": mean([r["usage"]["prompt"] for r in runs]),
            "mean_completion_tokens": mean([r["usage"]["completion"] for r in runs]),
            "mean_calls": mean([r["usage"]["calls"] for r in runs]),
            "mean_seconds": mean([r["seconds"] for r in runs]),
            "estimated": any(r["usage"]["estimated"] for r in runs)}


async def run_one(svc, name: str, goal: str) -> dict:
    from aethel.runtime.store import TERMINAL_STATES
    reset_workspace()
    conv = svc.conversations.create(title=f"eval-tokens: {name}")
    t0 = time.monotonic()
    task_id = await svc.engine.start(conversation_id=conv.id, goal=goal)
    while time.monotonic() - t0 < TASK_TIMEOUT_S:
        for approval in svc.approvals.pending_for(task_id):
            print(f"      approving once: {approval.tool} {approval.summary}")
            await svc.approvals.resolve(approval.approval_id, "allow_once")
        task = svc.tasks.get(task_id)
        if task.state in TERMINAL_STATES:
            break
        await asyncio.sleep(0.5)
    else:
        await svc.engine.cancel(task_id)
    await svc.engine.wait_idle()
    task = svc.tasks.get(task_id)
    return {"task": name, "state": task.state, "error": task.error, "seconds": round(time.monotonic() - t0, 1),
            "usage": svc.usage.task_totals(task_id)}


async def main(reps: int) -> None:
    from aethel.paths import aethel_home
    from aethel.services import build_services

    svc = build_services()
    svc.mcp.start(svc.mcp_servers)
    if not await svc.mcp.wait_ready("windows", 180):
        print("Desktop control didn't start (see ~/.aethel/logs/mcp-windows.log).")
        return
    saved = svc.settings.get().model_dump()
    result = {"reps": reps, "arms": {}}
    try:
        for masking in (False, True):
            arm = "masking on" if masking else "masking off"
            svc.settings.update({"use_learned_skills": False, "token_saving": {"mask_superseded": masking}})
            runs = []
            for name, goal in tasks():
                for rep in range(reps):
                    print(f"[{arm}] {name} #{rep + 1}")
                    r = await run_one(svc, name, goal)
                    print(f"      {r['state']} in {r['seconds']} s, {r['usage']['prompt']} in / "
                          f"{r['usage']['completion']} out, {r['usage']['calls']} calls")
                    runs.append(r)
            result["arms"][arm] = {"summary": summarise(runs), "runs": runs}
    finally:
        svc.settings.update({"use_learned_skills": saved["use_learned_skills"],
                             "token_saving": saved["token_saving"]})
        shutil.rmtree(WORK, ignore_errors=True)
        await svc.engine.shutdown()
        await svc.mcp.stop()
        svc.close()
    for arm, data in result["arms"].items():
        print(arm, data["summary"])
    out = aethel_home() / "eval" / "tokens.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=2)
    asyncio.run(main(parser.parse_args().reps))
