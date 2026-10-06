import re

from ..providers.base import ToolSpec
from .base import Tool
from .compact import compact_spec
from .descriptions import SHORT

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")  # the OpenAI function-name rule


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._compacted: dict[str, tuple[Tool, ToolSpec]] = {}

    def register(self, tool: Tool) -> None:
        if not NAME_RE.match(tool.name):
            raise ValueError(f"invalid tool name: {tool.name!r}")
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self, group: str | None = None) -> list[str]:
        return [t.name for t in self._tools.values() if group is None or t.toolgroup == group]

    def groups(self) -> set[str]:
        """The tool groups with at least one tool registered right now."""
        return {t.toolgroup for t in self._tools.values()}

    def specs(self, groups: set[str] | frozenset[str] | None = None, compact: bool = False) -> list[ToolSpec]:
        """What the model is offered, in registration order (a stable order keeps the provider's cached prefix).
        groups: only these tool groups (None: all). compact: shorter schemas that say the same (tools/compact.py)."""
        tools = [t for t in self._tools.values() if groups is None or t.toolgroup in groups]
        if not compact:
            return [ToolSpec(t.name, t.description, t.parameters) for t in tools]
        return [self._compact_spec(t) for t in tools]

    def _compact_spec(self, tool: Tool) -> ToolSpec:
        cached = self._compacted.get(tool.name)
        if cached is None or cached[0] is not tool:  # a tool that was registered again is compacted again
            cached = self._compacted[tool.name] = (
                tool, compact_spec(ToolSpec(tool.name, tool.description, tool.parameters), SHORT.get(tool.name)))
        return cached[1]
