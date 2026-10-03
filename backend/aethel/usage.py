"""Token accounting (token-efficiency spec §3): one row per LLM call, with what the provider reported
(or a chars/4 estimate, flagged) and a rough breakdown of where the prompt's tokens went."""
import json
import logging
from datetime import datetime, timedelta, timezone

from .providers.base import ChatMessage, ToolSpec, Usage
from .store.db import Database
from .store.repos import new_id, now_iso

log = logging.getLogger("aethel.usage")
PURPOSES = ("chat_reply", "fact_extract", "plan", "execute", "final_summary", "reflect", "vision_locate", "web_chat")


def _tokens(chars: int) -> int:
    return chars // 4


def estimate_breakdown(messages: list[ChatMessage], tools: list[ToolSpec] | None) -> dict[str, int]:
    """Estimated prompt tokens by part. 'observations' are tool results; 'history' is everything else
    after the system message."""
    parts = {"system": 0, "tools": 0, "history": 0, "observations": 0}
    for m in messages:
        size = len(m.content or "") + sum(len(c.arguments) + len(c.name) for c in m.tool_calls or [])
        key = "system" if m.role == "system" else "observations" if m.role == "tool" else "history"
        parts[key] += size
    if tools:
        parts["tools"] = len(json.dumps([{"name": t.name, "description": t.description, "parameters": t.parameters}
                                         for t in tools]))
    return {k: _tokens(v) for k, v in parts.items()}


class UsageLog:
    def __init__(self, db: Database):
        self.db = db

    def record(self, *, role: str, purpose: str, provider: str, model: str, ref: dict | None, usage: Usage | None,
               breakdown: dict, status: str, latency_ms: int, started: bool = True) -> None:
        """Never raises: accounting must not break a reply."""
        try:
            if usage is not None:
                prompt, completion, cached, estimated = usage.prompt, usage.completion, usage.cached, 0
            elif started:  # the call ran, the provider just didn't say: estimate the prompt
                prompt, completion, cached, estimated = sum(breakdown.values()), None, None, 1
            else:          # it failed before anything happened: no tokens were spent
                prompt = completion = cached = None
                estimated = 0
            ref = ref or {}
            self.db.execute(
                "INSERT INTO llm_calls (id, role, purpose, provider, model, task_id, message_id, prompt_tokens,"
                " completion_tokens, cached_tokens, estimated, breakdown, status, latency_ms, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id("llm"), role, purpose, provider, model, ref.get("task_id"), ref.get("message_id"), prompt,
                 completion, cached, estimated, json.dumps(breakdown), status, latency_ms, now_iso()))
        except Exception:
            log.exception("couldn't record an LLM call")

    @staticmethod
    def _sum(rows) -> dict:
        return {"prompt": sum(r["prompt_tokens"] or 0 for r in rows),
                "completion": sum(r["completion_tokens"] or 0 for r in rows),
                "cached": sum(r["cached_tokens"] or 0 for r in rows),
                "calls": sum(1 for r in rows if r["prompt_tokens"] is not None),
                "estimated": any(r["estimated"] for r in rows)}

    def task_totals(self, task_id: str) -> dict:
        return self._sum(self.db.query("SELECT * FROM llm_calls WHERE task_id = ?", (task_id,)))

    def totals(self, days: int) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db.query("SELECT * FROM llm_calls WHERE created_at >= ? ORDER BY created_at", (since,))
        by_purpose: dict[str, list] = {}
        by_day: dict[str, list] = {}
        for r in rows:
            by_purpose.setdefault(r["purpose"], []).append(r)
            by_day.setdefault(r["created_at"][:10], []).append(r)
        return {"days": days, "total": self._sum(rows),
                "by_purpose": {p: self._sum(rs) for p, rs in by_purpose.items()},
                "by_day": [{"day": d, **self._sum(rs)} for d, rs in sorted(by_day.items())]}
