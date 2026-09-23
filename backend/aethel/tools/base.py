from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

RiskTier = Literal["read", "write", "irreversible"]
Verdict = Literal["allow", "ask", "deny"]


@dataclass
class ToolContext:
    task_id: str | None
    tainted: bool = False  # becomes True once untrusted content entered the task


@dataclass
class ToolResult:
    ok: bool
    content: str             # what the model sees (wrapped as untrusted by the engine if flagged)
    untrusted: bool = False  # came from outside: file contents, command output, apps, web


@dataclass
class Assessment:
    verdict: Verdict
    reason: str
    target: str  # what the action touches, shown to the user (a path, a command…)


Handler = Callable[[dict, ToolContext], Awaitable[ToolResult]]
Assessor = Callable[[dict], Assessment]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema of the arguments object
    tier: RiskTier
    handler: Handler
    assess: Assessor
