"""Word, Excel and PowerPoint through the in-house COM server (spec §4.2).

Paths are resolved and permission-checked here, in the backend, and a save
snapshots the target first so "Undo file changes" covers Office files too."""
import os
import sys
from pathlib import Path

from mcp import types as mt

from ..safety.changes import ChangeLog
from ..safety.permissions import Permissions
from .base import Assessment, Tool, ToolContext, ToolResult
from .local_fs import NEED_ABSOLUTE
from .mcp_hub import ServerSpec
from .paths import resolve_user_path

SERVER = "office"
BACKEND_DIR = Path(__file__).resolve().parents[2]

# name -> (tier, app, path check: None | "read" | "write")
OFFICE_TOOLS = {
    "word_new": ("write", "word", None),
    "word_open": ("read", "word", "read"),
    "word_type": ("write", "word", None),
    "word_read": ("read", "word", None),
    "word_save": ("write", "word", "write"),
    "excel_open": ("read", "excel", "read"),
    "excel_read": ("read", "excel", None),
    "excel_write": ("write", "excel", None),
    "excel_save": ("write", "excel", "write"),
    "ppt_new": ("write", "powerpoint", None),
    "ppt_add_slide": ("write", "powerpoint", None),
    "ppt_save": ("write", "powerpoint", "write"),
}


class Office:
    def __init__(self, perms: Permissions | None, changes: ChangeLog | None):
        self.perms = perms
        self.changes = changes
        self.hub = None

    def _tool(self, remote: mt.Tool) -> Tool:
        name = remote.name
        tier, app, path_mode = OFFICE_TOOLS[name]

        def assess(args: dict) -> Assessment:
            summary = f"{name.split('_', 1)[1].replace('_', ' ').capitalize()} ({app})"
            raw = args.get("path")
            if path_mode is None or (not raw and name == "excel_open"):
                return Assessment("allow", "", summary)
            path = resolve_user_path(raw)
            if path is None:
                return Assessment("deny", NEED_ABSOLUTE, str(raw or "(no path)"))
            d = self.perms.check_path(str(path), path_mode)
            return Assessment(d.verdict, d.reason, f"{summary}: {path}")

        def scope(args: dict) -> str:
            path = resolve_user_path(args.get("path")) if path_mode == "write" else None
            return os.path.normcase(str(path.parent)) if path is not None else app

        async def handler(args: dict, ctx: ToolContext) -> ToolResult:
            args = dict(args)
            if path_mode is not None and args.get("path"):
                path = resolve_user_path(args["path"])
                if path is None:
                    return ToolResult(False, NEED_ABSOLUTE)
                args["path"] = str(path)  # exactly the path that was checked
                if path_mode == "write":
                    self.changes.record_before_write(str(path), ctx.task_id)
                    path.parent.mkdir(parents=True, exist_ok=True)
            return await self.hub.call(SERVER, name, args)

        return Tool(name, remote.description or name, remote.inputSchema or {"type": "object"}, tier, handler,
                    assess, grant_scope=scope, group="office", toolgroup="office")

    def adapt(self, hub, remote_tools: list[mt.Tool]) -> list[Tool]:
        self.hub = hub
        return [self._tool(t) for t in remote_tools if t.name in OFFICE_TOOLS]


def office_spec(office: Office) -> ServerSpec | None:
    if os.name != "nt":
        return None
    return ServerSpec(SERVER, [sys.executable, "-m", "aethel.mcp_servers.office"], office.adapt,
                      env={"PYTHONPATH": str(BACKEND_DIR)})
