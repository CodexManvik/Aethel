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

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self, compact: bool = False) -> list[ToolSpec]:
        """What the model is offered. compact: shorter schemas that say the same (tools/compact.py)."""
        if not compact:
            return [ToolSpec(t.name, t.description, t.parameters) for t in self._tools.values()]
        return [self._compact_spec(t) for t in self._tools.values()]

    def _compact_spec(self, tool: Tool) -> ToolSpec:
        cached = self._compacted.get(tool.name)
        if cached is None or cached[0] is not tool:  # a tool that was registered again is compacted again
            cached = self._compacted[tool.name] = (
                tool, compact_spec(ToolSpec(tool.name, tool.description, tool.parameters), SHORT.get(tool.name)))
        return cached[1]
