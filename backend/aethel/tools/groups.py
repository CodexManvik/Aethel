"""Tool groups on demand (token spec §6). A task carries the core tools and a one-line catalogue of the rest;
`use_tools(group)` adds a group from the next model turn on. Nothing is unreachable: the catalogue is always in
the prompt, and a direct call to a tool of an inactive group adds the group itself (runtime/engine.py)."""
from ..providers.base import ToolSpec

# group -> what it's for, so the model knows when to ask
GROUPS: dict[str, str] = {
    "desktop_extra": "more desktop control: drag, multi-select, multi-field typing, monitors",
    "office": "Word, Excel and PowerPoint automation (create, read, write and save documents, sheets and slides)",
    "files": "bulk file work: search inside many files, file info, edit part of a file",
    "web": "search the web and read pages, with citations",
    "browser": "your own background web browser: open pages, click, type, fill forms",
}
CORE = frozenset({"core"})
CATALOGUE_START = "More tools on request"  # what catalogue_line() opens with, so it can be found and replaced


def use_tools_spec(options: list[str]) -> ToolSpec:
    """`use_tools`, offering only the groups that can still be asked for."""
    return ToolSpec("use_tools", "Add a group of tools for the rest of this task.",
                    {"type": "object", "properties": {"group": {"type": "string", "enum": options}},
                     "required": ["group"]})


USE_TOOLS = use_tools_spec(list(GROUPS))


def inactive_groups(available: set[str] | frozenset[str], active: set[str] | frozenset[str]) -> list[str]:
    """The groups that exist but aren't active yet, in the catalogue's order."""
    return [g for g in [*GROUPS, *sorted(available - GROUPS.keys())] if g in available and g not in active]


def catalogue_line(available: set[str] | frozenset[str], active: set[str] | frozenset[str]) -> str:
    """One line naming what can still be asked for, or "" when there is nothing left."""
    left = inactive_groups(available, active)
    if not left:
        return ""
    return CATALOGUE_START + " (call use_tools): " + "; ".join(
        f"{g} ({GROUPS[g]})" if g in GROUPS else g for g in left)
