import pytest

from aethel.safety.policy import decide
from aethel.tools.base import Assessment, Tool, ToolResult


async def _noop(args, ctx):
    return ToolResult(True, "")


def _tool(tier):
    return Tool(name="t", description="", parameters={}, tier=tier, handler=_noop,
                assess=lambda a: Assessment("allow", "", "x"))


@pytest.mark.parametrize("tier,base,tainted,grants,expected", [
    ("read", "allow", False, set(), "allow"),
    ("read", "allow", True, set(), "allow"),                # reading never escalates
    ("write", "allow", False, set(), "allow"),
    ("write", "allow", True, set(), "ask"),                 # acts on untrusted content
    ("write", "allow", True, {("t", "scope-a")}, "allow"),  # granted for this task, this scope
    ("write", "allow", True, {("t", "scope-b")}, "ask"),    # a grant elsewhere doesn't cover it
    ("write", "ask", False, set(), "ask"),
    ("write", "ask", False, {("t", "scope-a")}, "allow"),
    ("write", "ask", False, {("t", "scope-b")}, "ask"),
    ("write", "ask", False, {("other", "scope-a")}, "ask"),  # another tool's grant
    ("irreversible", "allow", False, {("t", "scope-a")}, "ask"),  # never covered by grants
    ("write", "deny", False, {("t", "scope-a")}, "deny"),         # deny always wins
])
def test_decide(tier, base, tainted, grants, expected):
    result = decide(_tool(tier), Assessment(base, "because", "x"), tainted=tainted, grants=grants, scope="scope-a")
    assert result.verdict == expected
    assert result.target == "x"
