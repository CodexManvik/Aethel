"""
skill_engine.py

RSM Phase 3 — skill writing.

After the agent loop completes a task that used tools, this runs a background
reflection pass: it hands the model the user request, the tool calls that were
made, and their outcomes, and asks it to distill a reusable *skill* — a
procedural how-to for the next time a similar task comes up (Voyager-style).

The distilled skill is written to the markdown knowledge store as a
quarantined skill for the user to review/approve, exactly like rules. Skills
that lead to successful future runs gain confidence via record_outcome;
repeatedly-failing ones auto-deprecate.

Runs entirely as an asyncio background task; never blocks the chat stream.
"""

import re
import json
import httpx
import yaml
from typing import Optional

from config import (
    LLAMA_BASE_URL,
    LLAMA_TIMEOUT,
    RULE_ENGINE_REFLECTION_TEMPERATURE,
)
import knowledge_store as ks

# Cosine similarity above which a newly distilled skill is considered a
# duplicate of one already in the library (reinforce instead of insert).
SKILL_DUPLICATE_THRESHOLD = 0.90

VALID_SKILL_CATEGORIES = {
    "file_management",
    "code_task",
    "web_research",
    "system_admin",
    "data_processing",
    "general_task",
}

_SKILL_SYSTEM = """\
You are a skill-distillation engine for an AI agent that can read/write files,
run shell commands, and fetch web pages. You observe a COMPLETED task and write
a reusable procedural skill so the agent handles similar tasks better next time.

RULES FOR YOUR OUTPUT:
1. Output ONLY one valid YAML block. No prose outside the YAML.
2. If the task was trivial, chit-chat, or used no tools meaningfully, output: null
3. The skill must generalize — describe the PROCEDURE, not the specific paths or
   values from this one run.
4. Use this exact schema:

```yaml
title: "<short imperative name, e.g. 'Summarize a folder of text files'>"
category: "<one of: file_management | code_task | web_research | system_admin | data_processing | general_task>"
scope: "<task_type:<specific_type>>"
confidence: <float 0.3-0.7>
when_to_use: "<the trigger conditions for this skill>"
steps: |
  1. <step using the available tools>
  2. <step>
tools_used: "<comma-separated tool names actually needed>"
failure_modes: "<pitfalls to avoid, from what went wrong or could go wrong>"
```

QUALITY CONSTRAINTS:
- 'confidence' <= 0.5 since this is learned from a single run.
- 'steps' must reference the real tools (fs.read, fs.write, fs.list, fs.delete, shell, fetch).
- Do not invent tools that were not used or available.
"""

_SKILL_USER = """\
USER REQUEST:
{user_request}

TOOL CALLS MADE (in order):
{tool_trace}

FINAL OUTCOME: {outcome}

Distill a reusable skill from this task, or output null if there is nothing
worth generalizing.
"""


async def maybe_write_skill(
    user_request: str,
    tool_calls: list[dict],
    succeeded: bool,
    source_interaction_id: str,
) -> Optional[str]:
    """
    Reflects on a completed agent task and writes a quarantined skill.

    Returns the new skill id, or None if no skill was written (task too
    trivial, no tools used, malformed model output, or LLM unavailable).
    """
    # Only bother distilling from tasks that actually did something with tools.
    if not tool_calls:
        return None

    tool_trace = _format_tool_trace(tool_calls)
    outcome = "SUCCESS" if succeeded else "PARTIAL/FAILED"

    raw = await _call_llm(
        _SKILL_SYSTEM,
        _SKILL_USER.format(
            user_request=user_request[:1500],
            tool_trace=tool_trace,
            outcome=outcome,
        ),
    )
    if not raw:
        return None

    parsed = _parse_skill(raw)
    if not parsed:
        return None

    err = _validate_skill(parsed)
    if err:
        return None

    # Deduplicate: if we've already learned essentially this skill, reinforce it
    # rather than adding a near-identical copy. An unbounded library of
    # duplicates dilutes retrieval and buries the genuinely distinct skills.
    dup_text = ks.skill_embed_text(
        title=str(parsed["title"]),
        when_to_use=str(parsed.get("when_to_use", "")),
        steps=str(parsed.get("steps", "")),
        tools_used=str(parsed.get("tools_used", "")),
        failure_modes=str(parsed.get("failure_modes", "")),
    )
    try:
        existing = ks.find_duplicate(dup_text, doc_type=ks.TYPE_SKILL, threshold=SKILL_DUPLICATE_THRESHOLD)
    except Exception:
        existing = None
    if existing:
        ks.reinforce(existing["id"])
        print(
            f"[SKILL] '{parsed['title']}' duplicates existing "
            f"'{existing.get('title', existing['id'])}' "
            f"(sim {existing.get('_similarity')}) — reinforced instead."
        )
        return None

    return ks.insert_skill(
        title=str(parsed["title"]),
        category=str(parsed["category"]),
        confidence=float(parsed["confidence"]),
        scope=str(parsed.get("scope", "task_type:general")),
        when_to_use=str(parsed.get("when_to_use", "")),
        steps=str(parsed.get("steps", "")),
        tools_used=str(parsed.get("tools_used", "")),
        failure_modes=str(parsed.get("failure_modes", "")),
        source_interaction_id=source_interaction_id,
        status=ks.STATUS_QUARANTINED,
    )


def _format_tool_trace(tool_calls: list[dict]) -> str:
    lines = []
    for i, c in enumerate(tool_calls, 1):
        args = ", ".join(f"{k}={v}" for k, v in c.get("args", {}).items())
        status = "ok" if c.get("ok") else "error"
        lines.append(f"{i}. {c.get('tool', '?')}({args}) -> {status}")
    return "\n".join(lines) or "(none)"


async def _call_llm(system_prompt: str, user_prompt: str) -> Optional[str]:
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": RULE_ENGINE_REFLECTION_TEMPERATURE,
        "max_tokens": 800,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=LLAMA_TIMEOUT) as client:
            resp = await client.post(f"{LLAMA_BASE_URL}/chat/completions", json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def _parse_skill(raw: str) -> Optional[dict]:
    cleaned = re.sub(r"```(?:yaml)?", "", raw, flags=re.IGNORECASE).replace("```", "").strip()
    if cleaned.lower() in ("null", "none", "~", ""):
        return None
    try:
        parsed = yaml.safe_load(cleaned)
    except yaml.YAMLError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _validate_skill(skill: dict) -> Optional[str]:
    for field in ("title", "category", "confidence", "steps"):
        if not skill.get(field):
            return f"missing {field}"
    if skill["category"] not in VALID_SKILL_CATEGORIES:
        return f"bad category {skill['category']}"
    try:
        c = float(skill["confidence"])
        if not (0.0 <= c <= 1.0):
            return "confidence out of range"
    except (ValueError, TypeError):
        return "confidence not a float"
    if len(str(skill["title"])) < 4:
        return "title too short"
    return None
