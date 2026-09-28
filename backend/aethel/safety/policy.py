"""Turns a tool's static risk tier plus its per-call assessment into the
final verdict (spec §4.3)."""
from ..tools.base import Assessment, Tool

_RANK = {"read": 0, "write": 1, "irreversible": 2}


def decide(tool: Tool, assessment: Assessment, *, tainted: bool, grants: set[tuple[str, str]],
           scope: str) -> Assessment:
    """`grants` holds the (grant key, scope) pairs the user allowed for this
    task; `scope` is this call's scope (Tool.scope_for). The result carries the
    effective tier: a call may raise its tool's tier, never lower it."""
    tier = max(tool.tier, assessment.tier or tool.tier, key=_RANK.__getitem__)
    if assessment.verdict == "deny":
        return Assessment("deny", assessment.reason, assessment.target, tier)
    if tier == "irreversible":
        return Assessment("ask", assessment.reason or "This can't be undone.", assessment.target, tier)
    granted = (tool.grant_key, scope) in grants
    if assessment.verdict == "ask":
        return Assessment("allow", "Allowed for this task.", assessment.target, tier) if granted else             Assessment("ask", assessment.reason, assessment.target, tier)
    if tier == "write" and tainted and not granted:
        return Assessment("ask", "This task has read outside content; confirming before it changes anything.",
                          assessment.target, tier)
    return Assessment(assessment.verdict, assessment.reason, assessment.target, tier)
