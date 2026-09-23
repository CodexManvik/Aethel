# rule_engine.py
"""
Meta-cognitive consolidation engine.

After every N interactions (configurable via RULE_ENGINE_CONSOLIDATION_INTERVAL),
this module runs a background async pass using the local llama.cpp inference server.

The model receives the last N conversation turns plus the currently approved rules
and is asked to generate 0-3 structured behavioral rules in a strict YAML+Markdown
schema. Each output rule is parsed, validated, and inserted into rule_store with
status='quarantined' for user review.

Design constraints:
- Does NOT block the chat stream. Runs entirely as a background asyncio.Task.
- Uses httpx.AsyncClient with the same LLAMA_BASE_URL as generation.py.
- All LLM calls use low temperature (0.35) to reduce hallucinated rule content.
- Malformed rule outputs are discarded silently; partial batches are accepted.
"""

import asyncio
import json
import re
import uuid
import yaml
import httpx
from datetime import datetime, timezone
from typing import Optional

from config import (
    LLAMA_BASE_URL,
    LLAMA_TIMEOUT,
    RULE_ENGINE_CONSOLIDATION_INTERVAL,
    RULE_ENGINE_MAX_RULES_PER_PASS,
    RULE_ENGINE_REFLECTION_TEMPERATURE,
)
from rule_store import insert_rule, get_all_rules
import knowledge_store as ks

# Cosine similarity above which a distilled rule counts as one we already have.
RULE_DUPLICATE_THRESHOLD = 0.90

# ─────────────────────────────────────────────
# Valid values for schema enforcement
# ─────────────────────────────────────────────
VALID_CATEGORIES = {"communication_style", "task_behavior", "persona_trait", "boundary"}
VALID_SCOPE_PREFIXES = {"global", "conversation", "task_type"}

# ─────────────────────────────────────────────
# Reflection prompt template
# ─────────────────────────────────────────────
_REFLECTION_SYSTEM = """\
You are a meta-cognitive behavioral analyst. Your job is to observe a conversation \
and extract reusable behavioral rules that will improve future responses.

RULES FOR YOUR OUTPUT:
1. Output ONLY valid YAML blocks. No prose, no preamble, no explanation outside the YAML.
2. Output between 0 and {max_rules} rules. If the conversation has nothing useful to encode, output: []
3. Each rule must follow this exact schema:

```yaml
- body: "<Natural language instruction for the AI to follow in future responses>"
  rationale: "<Why this rule is useful based on what happened in the conversation>"
  category: "<one of: communication_style | task_behavior | persona_trait | boundary>"
  confidence: <float between 0.3 and 1.0>
  scope: "<one of: global | conversation | task_type:<specific_type>>"
  example_before: "<What the AI did that this rule corrects or improves>"
  example_after: "<What the AI should do instead>"
```

QUALITY CONSTRAINTS:
- 'body' must be a direct behavioral instruction, not a meta-observation.
- 'confidence' must be <= 0.5 if the rule is inferred from a single interaction.
- Do not generate rules that contradict the existing approved rules listed below.
- Do not generate duplicate rules.
"""

_REFLECTION_USER = """\
CURRENTLY APPROVED RULES (do not contradict or duplicate these):
{approved_rules_summary}

LAST {n} CONVERSATION TURNS:
{conversation_turns}

Generate behavioral rules from the above conversation. Output ONLY the YAML array.
"""


# ─────────────────────────────────────────────
# Consolidation pass
# ─────────────────────────────────────────────

async def run_consolidation_pass(
    session_id: str,
    history: list[dict],
    notification_callback: Optional[callable] = None,
) -> list[str]:
    """
    Runs the meta-cognitive consolidation pass over the provided conversation history.

    Args:
        session_id:            The current session ID (stored as source_interaction_id on rules).
        history:               Last N conversation turns as list of {role, content} dicts.
        notification_callback: Optional async callable invoked with a count of new rules
                               written to quarantine, for SSE notification to the frontend.

    Returns:
        List of rule IDs that were inserted (all in quarantined status).
    """
    approved_rules = get_all_rules(status="approved")
    approved_summary = _format_approved_rules_for_prompt(approved_rules)
    conversation_text = _format_history_for_prompt(history)

    system_prompt = _REFLECTION_SYSTEM.format(
        max_rules=RULE_ENGINE_MAX_RULES_PER_PASS
    )
    user_prompt = _REFLECTION_USER.format(
        approved_rules_summary=approved_summary,
        n=len(history),
        conversation_turns=conversation_text,
    )

    raw_output = await _call_llm(system_prompt, user_prompt)
    if not raw_output:
        return []

    parsed_rules = _parse_rule_output(raw_output)
    if not parsed_rules:
        return []

    inserted_ids: list[str] = []
    for rule_data in parsed_rules[:RULE_ENGINE_MAX_RULES_PER_PASS]:
        validation_error = _validate_rule(rule_data)
        if validation_error:
            continue  # silently discard malformed rules

        # Skip near-duplicates of rules we already hold (the reflection prompt
        # asks the model not to repeat itself, but small models often do).
        # Rediscovering a rule is evidence it matters, so reinforce instead.
        try:
            dup = ks.find_duplicate(
                ks.rule_embed_text(
                    body=rule_data["body"],
                    rationale=rule_data["rationale"],
                    ex_before=rule_data.get("example_before", ""),
                    ex_after=rule_data.get("example_after", ""),
                ),
                doc_type=ks.TYPE_RULE,
                threshold=RULE_DUPLICATE_THRESHOLD,
            )
        except Exception:
            dup = None
        if dup:
            ks.reinforce(dup["id"])
            continue

        rule_id = insert_rule(
            category=rule_data["category"],
            confidence=float(rule_data["confidence"]),
            scope=rule_data["scope"],
            body=rule_data["body"],
            rationale=rule_data["rationale"],
            examples_before=rule_data.get("example_before", ""),
            examples_after=rule_data.get("example_after", ""),
            source_interaction_id=session_id,
        )
        inserted_ids.append(rule_id)

    if inserted_ids and notification_callback:
        try:
            await notification_callback(len(inserted_ids))
        except Exception:
            pass  # notification failure must not crash the consolidation pass

    return inserted_ids


# ─────────────────────────────────────────────
# Scheduler — tracks interaction count per session
# ─────────────────────────────────────────────

class ConsolidationScheduler:
    """
    Tracks per-session interaction counts and fires the consolidation pass
    every RULE_ENGINE_CONSOLIDATION_INTERVAL turns.

    Usage in server.py:
        scheduler = ConsolidationScheduler()
        # Inside the chat endpoint, after appending to session history:
        asyncio.create_task(
            scheduler.tick(session_id, session_history, sse_notify_fn)
        )
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._in_flight: set[str] = set()

    async def tick(
        self,
        session_id: str,
        history: list[dict],
        notification_callback: Optional[callable] = None,
    ) -> None:
        """
        Increments the interaction counter for the session and fires the
        consolidation pass if the threshold is reached. Skips if a pass
        is already in-flight for this session to prevent overlapping runs.
        """
        self._counters[session_id] = self._counters.get(session_id, 0) + 1

        if self._counters[session_id] < RULE_ENGINE_CONSOLIDATION_INTERVAL:
            return

        if session_id in self._in_flight:
            return  # already running for this session

        self._counters[session_id] = 0
        self._in_flight.add(session_id)

        try:
            await run_consolidation_pass(
                session_id=session_id,
                history=history[-RULE_ENGINE_CONSOLIDATION_INTERVAL:],
                notification_callback=notification_callback,
            )
        finally:
            self._in_flight.discard(session_id)

    def reset(self, session_id: str) -> None:
        """Resets the counter for a session (e.g., on session teardown)."""
        self._counters.pop(session_id, None)
        self._in_flight.discard(session_id)


# ─────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────

async def _call_llm(system_prompt: str, user_prompt: str) -> Optional[str]:
    """
    Calls the local llama.cpp server with the reflection prompts.
    Uses a lower temperature than the chat endpoint to reduce hallucinated rules.
    Returns the raw text content of the model response, or None on failure.
    """
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature":        RULE_ENGINE_REFLECTION_TEMPERATURE,
        "max_tokens":         1200,
        "stream":             False,
    }
    try:
        async with httpx.AsyncClient(timeout=LLAMA_TIMEOUT) as client:
            response = await client.post(
                f"{LLAMA_BASE_URL}/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def _parse_rule_output(raw: str) -> list[dict]:
    """
    Extracts and parses the YAML rule array from the LLM output.
    Handles models that wrap output in markdown code fences.
    Returns an empty list on any parse failure.
    """
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:yaml)?", "", raw, flags=re.IGNORECASE).replace("```", "").strip()

    # If the model output something like "[]" for no rules
    if cleaned == "[]" or cleaned == "[ ]":
        return []

    try:
        parsed = yaml.safe_load(cleaned)
        if isinstance(parsed, list):
            return parsed
        return []
    except yaml.YAMLError:
        return []


def _validate_rule(rule: dict) -> Optional[str]:
    """
    Validates a parsed rule dict against the schema.
    Returns an error string if invalid, None if valid.
    """
    required_fields = ("body", "rationale", "category", "confidence", "scope")
    for field in required_fields:
        if field not in rule or not rule[field]:
            return f"Missing required field: {field}"

    if rule["category"] not in VALID_CATEGORIES:
        return f"Invalid category: {rule['category']}"

    try:
        conf = float(rule["confidence"])
        if not (0.0 <= conf <= 1.0):
            return f"Confidence out of range: {conf}"
    except (ValueError, TypeError):
        return "Confidence is not a valid float"

    scope = str(rule["scope"])
    scope_base = scope.split(":")[0]
    if scope_base not in VALID_SCOPE_PREFIXES:
        return f"Invalid scope: {scope}"

    if len(str(rule["body"])) < 10:
        return "Rule body too short to be meaningful"

    return None


def _format_approved_rules_for_prompt(rules: list[dict]) -> str:
    """Formats approved rules as a compact numbered list for the reflection prompt."""
    if not rules:
        return "None yet."
    lines = []
    for i, rule in enumerate(rules[:20], 1):  # cap at 20 to avoid context overflow
        lines.append(f"{i}. [{rule.get('category', 'unknown')}] {rule.get('body', '')}")
    return "\n".join(lines)


def _format_history_for_prompt(history: list[dict]) -> str:
    """Formats conversation history as a readable turn-by-turn transcript."""
    lines = []
    for msg in history:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        # Truncate individual turns to prevent context overflow
        if len(content) > 500:
            content = content[:497] + "..."
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)
