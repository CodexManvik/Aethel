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
    ("read", "allow", True, set(), "allow"),          # reading never escalates
    ("write", "allow", False, set(), "allow"),
    ("write", "allow", True, set(), "ask"),           # acts on untrusted content
    ("write", "allow", True, {"t"}, "allow"),         # granted for this task
    ("write", "ask", False, set(), "ask"),
    ("write", "ask", False, {"t"}, "allow"),
    ("irreversible", "allow", False, {"t"}, "ask"),   # never covered by grants
    ("write", "deny", False, {"t"}, "deny"),          # deny always wins
])
def test_decide(tier, base, tainted, grants, expected):
    result = decide(_tool(tier), Assessment(base, "because", "x"), tainted=tainted, grants=grants)
    assert result.verdict == expected
    assert result.target == "x"
