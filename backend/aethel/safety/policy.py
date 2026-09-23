"""Turns a tool's static risk tier plus its per-call assessment into the
final verdict (spec §4.3)."""
from ..tools.base import Assessment, Tool


def decide(tool: Tool, assessment: Assessment, *, tainted: bool, grants: set[tuple[str, str]],
           scope: str) -> Assessment:
    """`grants` holds the (tool name, scope) pairs the user allowed for this
    task; `scope` is this call's scope (Tool.scope_for)."""
    if assessment.verdict == "deny":
        return assessment
    if tool.tier == "irreversible":
        return Assessment("ask", assessment.reason or "This can't be undone.", assessment.target)
    granted = (tool.name, scope) in grants
    if assessment.verdict == "ask":
        return Assessment("allow", "Allowed for this task.", assessment.target) if granted else assessment
    if tool.tier == "write" and tainted and not granted:
        return Assessment("ask", "This task has read outside content; confirming before it changes anything.",
                          assessment.target)
    return assessment
