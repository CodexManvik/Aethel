"""
benchmark_agent.py

RSM Phase 4 — the A/B benchmark that produces the dissertation results table.

Runs a suite of filesystem/agent tasks through the real agent loop against the
live local LLM, in two conditions:

  baseline : no RSM knowledge injected (rules/skills suppressed)
  rsm      : approved rules + skills retrieved and injected into the prompt

Each task has a programmatic checker (file exists / content matches / answer
contains), so success is measured, not judged. Per task we record success,
steps taken, tool calls made, and wall time. Results are written to
backend/data/benchmark_results.json and printed as a table.

Usage (llama-server must be reachable, or will be auto-spawned):
    py backend/benchmark_agent.py                # both conditions
    py backend/benchmark_agent.py --mode rsm     # one condition only
    py backend/benchmark_agent.py --seed         # pre-seed demo skills first
    py backend/benchmark_agent.py --runs 3       # repeat suite N times

The sandbox lives at <project>/scratch/rsm_benchmark, which is inside both the
default read-allowed (project dir) and write-allowed (project scratch) paths of
the permission manifest, so the agent can operate there without loosening
security policy.
"""

import argparse
import asyncio
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import agent_loop
import knowledge_store as ks
from config import AGENT_MAX_STEPS

PROJECT_ROOT = Path(__file__).parent.parent
SANDBOX = PROJECT_ROOT / "scratch" / "rsm_benchmark"
RESULTS_PATH = Path(__file__).parent / "data" / "benchmark_results.json"

BASE_SYSTEM_PROMPT = (
    "You are a capable local AI agent. Complete the user's task using the "
    "available tools. Be precise with file paths and content."
)


# ─────────────────────────────────────────────────────────────
# Task suite
# ─────────────────────────────────────────────────────────────

def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def build_task_suite(sb: Path) -> list[dict]:
    """Ten checkable tasks of increasing multi-step difficulty."""

    def setup_read_report():
        _w(sb / "config.ini", "port=9137\nhost=localhost\n")

    def setup_find_string():
        _w(sb / "logs" / "a.log", "nothing here\n")
        _w(sb / "logs" / "b.log", "ERROR: disk almost full\n")
        _w(sb / "logs" / "c.log", "all fine\n")

    def setup_merge():
        _w(sb / "part1.txt", "The quick brown fox")
        _w(sb / "part2.txt", "jumps over the lazy dog")

    def setup_organize():
        _w(sb / "mixed" / "notes.txt", "text file")
        _w(sb / "mixed" / "data.csv", "a,b,c")
        _w(sb / "mixed" / "readme.txt", "another text")

    def setup_delete():
        _w(sb / "old" / "keep.txt", "keep me")
        _w(sb / "old" / "remove_me.tmp", "delete me")

    def setup_count():
        for i in range(4):
            _w(sb / "many" / f"f{i}.txt", f"file {i}")

    def setup_update():
        _w(sb / "version.txt", "version=1.0\n")

    def setup_todo():
        _w(sb / "todo.md", "# Todo\n- [ ] buy milk\n- [ ] fix bug\n")

    return [
        {
            "id": "write_file",
            "prompt": f"Create a file at {sb / 'hello.txt'} containing exactly the text: hello from the agent",
            "setup": lambda: None,
            "check": lambda final: "hello from the agent" in _read(sb / "hello.txt"),
        },
        {
            "id": "read_report",
            "prompt": f"Read the file {sb / 'config.ini'} and tell me which port number is configured.",
            "setup": setup_read_report,
            "check": lambda final: "9137" in final,
        },
        {
            "id": "count_files",
            "prompt": f"How many files are in the directory {sb / 'many'}? Answer with the number.",
            "setup": setup_count,
            "check": lambda final: "4" in final.split("[TOOL RESULT")[0] or " 4" in final[-200:],
        },
        {
            "id": "find_string",
            "prompt": f"One of the log files in {sb / 'logs'} contains an ERROR line. Which file is it? Read them to find out.",
            "setup": setup_find_string,
            "check": lambda final: "b.log" in final[-400:],
        },
        {
            "id": "merge_files",
            "prompt": (
                f"Read {sb / 'part1.txt'} and {sb / 'part2.txt'}, then write their combined "
                f"contents (part1 then part2, single space between) to {sb / 'merged.txt'}."
            ),
            "setup": setup_merge,
            "check": lambda final: "quick brown fox" in _read(sb / "merged.txt")
            and "lazy dog" in _read(sb / "merged.txt"),
        },
        {
            "id": "organize_by_ext",
            "prompt": (
                f"Organize {sb / 'mixed'}: copy every .txt file's content into a new file of the "
                f"same name under {sb / 'mixed' / 'text'}, then delete the original .txt files. "
                f"Leave other file types untouched."
            ),
            "setup": setup_organize,
            "check": lambda final: (sb / "mixed" / "text" / "notes.txt").exists()
            and (sb / "mixed" / "text" / "readme.txt").exists()
            and not (sb / "mixed" / "notes.txt").exists()
            and (sb / "mixed" / "data.csv").exists(),
        },
        {
            "id": "delete_file",
            "prompt": f"Delete the file {sb / 'old' / 'remove_me.tmp'} but leave everything else in that folder alone.",
            "setup": setup_delete,
            "check": lambda final: not (sb / "old" / "remove_me.tmp").exists()
            and (sb / "old" / "keep.txt").exists(),
        },
        {
            "id": "update_version",
            "prompt": f"Open {sb / 'version.txt'} and update the version from 1.0 to 2.0, keeping the same format.",
            "setup": setup_update,
            "check": lambda final: "version=2.0" in _read(sb / "version.txt"),
        },
        {
            "id": "complete_todo",
            "prompt": (
                f"In {sb / 'todo.md'}, mark the 'buy milk' item as done by changing its checkbox "
                f"from '- [ ]' to '- [x]'. Keep the rest of the file unchanged."
            ),
            "setup": setup_todo,
            "check": lambda final: "- [x] buy milk" in _read(sb / "todo.md")
            and "- [ ] fix bug" in _read(sb / "todo.md"),
        },
        {
            "id": "summarize_folder",
            "prompt": (
                f"List the files in {sb / 'logs'}, read each one, and write a one-line-per-file "
                f"summary to {sb / 'logs_summary.txt'}."
            ),
            "setup": setup_find_string,
            "check": lambda final: len(_read(sb / "logs_summary.txt").strip()) > 0,
        },
    ]


# ─────────────────────────────────────────────────────────────
# Demo skill seeding (clearly marked as seeded, quarantine-skipped)
# ─────────────────────────────────────────────────────────────

SEED_SKILLS = [
    dict(
        title="Modify part of an existing file safely",
        category="file_management",
        confidence=0.6,
        scope="task_type:file_edit",
        when_to_use="User asks to change, update, or mark something inside an existing file.",
        steps=(
            "1. fs.read the file to get its EXACT current content.\n"
            "2. Apply the requested change to that content in your head.\n"
            "3. fs.write the COMPLETE updated content back to the same path "
            "(fs.write replaces the whole file — never write only the changed line).\n"
            "4. Keep all unchanged lines byte-identical."
        ),
        tools_used="fs.read, fs.write",
        failure_modes="Writing only the changed fragment erases the rest of the file. Always write the full content.",
    ),
    dict(
        title="Combine or transform multiple files into one",
        category="file_management",
        confidence=0.6,
        scope="task_type:merge_files",
        when_to_use="User asks to merge, combine, or summarize several files into an output file.",
        steps=(
            "1. fs.list the directory if the exact filenames are not given.\n"
            "2. fs.read every input file one at a time.\n"
            "3. Build the combined/derived text.\n"
            "4. fs.write the result to the requested output path in ONE call."
        ),
        tools_used="fs.list, fs.read, fs.write",
        failure_modes="Do not guess file contents without reading; do not forget one of the inputs.",
    ),
    dict(
        title="Move a file's content to a new location",
        category="file_management",
        confidence=0.6,
        scope="task_type:move_files",
        when_to_use="User asks to move or relocate files (there is no move tool).",
        steps=(
            "1. fs.read the source file.\n"
            "2. fs.write the content to the destination path (directories are created automatically).\n"
            "3. Verify the write succeeded, then fs.delete the source file."
        ),
        tools_used="fs.read, fs.write, fs.delete",
        failure_modes="Deleting the source before the destination write is confirmed loses data.",
    ),
]


def seed_skills() -> int:
    n = 0
    for s in SEED_SKILLS:
        ks.insert_skill(
            **s,
            source_interaction_id="benchmark_seed",
            status=ks.STATUS_APPROVED,
        )
        n += 1
    return n


# ─────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────

def build_messages_for(task_prompt: str, with_rsm: bool) -> list[dict]:
    system = BASE_SYSTEM_PROMPT + "\n\n" + agent_loop.AGENT_TOOL_PROTOCOL
    if with_rsm:
        rules = ks.retrieve(task_prompt, doc_type=ks.TYPE_RULE, top_k=3)
        skills = ks.retrieve(task_prompt, doc_type=ks.TYPE_SKILL, top_k=2)
        if rules:
            system += "\n\n[BEHAVIORAL DIRECTIVES]\n" + "\n".join(
                f"- {r['body']}" for r in rules
            )
        if skills:
            system += "\n\n[LEARNED SKILLS]\nFollow these proven playbooks when they apply:\n"
            for s in skills:
                system += f"\n### {s.get('title', 'skill')}\n{s.get('_raw_body', '')}\n"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": task_prompt},
    ]


async def run_task(task: dict, with_rsm: bool) -> dict:
    # Fresh sandbox per task.
    shutil.rmtree(SANDBOX, ignore_errors=True)
    SANDBOX.mkdir(parents=True, exist_ok=True)
    task["setup"]()

    messages = build_messages_for(task["prompt"], with_rsm)
    sink: dict = {}
    t0 = time.perf_counter()
    text = ""
    async for chunk in agent_loop.run_agent_loop(messages, "default", result_sink=sink):
        text += chunk
    elapsed = time.perf_counter() - t0

    try:
        success = bool(task["check"](sink.get("model_text", text)))
    except Exception:
        success = False

    return {
        "task": task["id"],
        "condition": "rsm" if with_rsm else "baseline",
        "success": success,
        "steps": sink.get("steps", 0),
        "tool_calls": len(sink.get("tool_calls", [])),
        "budget_exhausted": sink.get("steps", 0) >= AGENT_MAX_STEPS,
        "seconds": round(elapsed, 2),
    }


async def run_suite(modes: list[str], runs: int) -> list[dict]:
    results: list[dict] = []
    for run_idx in range(runs):
        for mode in modes:
            with_rsm = mode == "rsm"
            for task in build_task_suite(SANDBOX):
                print(f"[run {run_idx + 1}/{runs}] [{mode}] {task['id']} ...", end=" ", flush=True)
                r = await run_task(task, with_rsm)
                r["run"] = run_idx + 1
                results.append(r)
                print(
                    ("OK" if r["success"] else "FAIL")
                    + f"  steps={r['steps']} tools={r['tool_calls']} {r['seconds']}s"
                )
    return results


def summarize(results: list[dict]) -> dict:
    summary: dict = {}
    for cond in ("baseline", "rsm"):
        rows = [r for r in results if r["condition"] == cond]
        if not rows:
            continue
        succ = [r for r in rows if r["success"]]
        summary[cond] = {
            "tasks_run": len(rows),
            "successes": len(succ),
            "success_rate": round(len(succ) / len(rows), 3),
            "avg_steps": round(sum(r["steps"] for r in rows) / len(rows), 2),
            "avg_steps_on_success": round(
                sum(r["steps"] for r in succ) / len(succ), 2
            ) if succ else None,
            "avg_seconds": round(sum(r["seconds"] for r in rows) / len(rows), 2),
            "budget_exhausted_count": sum(1 for r in rows if r["budget_exhausted"]),
        }
    return summary


def print_table(summary: dict) -> None:
    print("\n===== RSM BENCHMARK SUMMARY =====")
    header = f"{'metric':<28}" + "".join(f"{c:>12}" for c in summary)
    print(header)
    keys = [
        "tasks_run", "successes", "success_rate", "avg_steps",
        "avg_steps_on_success", "avg_seconds", "budget_exhausted_count",
    ]
    for k in keys:
        row = f"{k:<28}" + "".join(f"{str(summary[c].get(k)):>12}" for c in summary)
        print(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="RSM agent A/B benchmark")
    parser.add_argument("--mode", choices=["baseline", "rsm", "both"], default="both")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--seed", action="store_true", help="seed demo skills before running")
    args = parser.parse_args()

    if args.seed:
        n = seed_skills()
        print(f"Seeded {n} approved demo skills into the knowledge store.")

    modes = ["baseline", "rsm"] if args.mode == "both" else [args.mode]
    results = asyncio.run(run_suite(modes, args.runs))

    summary = summarize(results)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "runs": args.runs,
        "modes": modes,
        "max_steps": AGENT_MAX_STEPS,
        "summary": summary,
        "results": results,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print_table(summary)
    print(f"\nFull results written to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
