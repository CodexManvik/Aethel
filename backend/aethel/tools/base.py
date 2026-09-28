from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Union

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
    tier: RiskTier | None = None  # this call's tier, when it's riskier than the tool's (e.g. clicking "Send")


Handler = Callable[[dict, ToolContext], Awaitable[ToolResult]]
Assessor = Callable[[dict], Union[Assessment, Awaitable[Assessment]]]
GrantScope = Callable[[dict], str]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema of the arguments object
    tier: RiskTier
    handler: Handler
    assess: Assessor
    # What "Allow for this task" covers for one call (a folder, a command...).
    # None: the whole tool.
    grant_scope: GrantScope | None = None
    # Tools that share a group share grants: "Allow for this task" on one
    # desktop action in Notepad covers every desktop action in Notepad.
    group: str | None = None

    @property
    def grant_key(self) -> str:
        return self.group or self.name

    def scope_for(self, args: dict) -> str:
        return self.grant_scope(args) if self.grant_scope is not None else self.name
