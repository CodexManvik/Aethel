"""Desktop Commander (npm @wonderwhy-er/desktop-commander), for what our own
file tools lack: surgical edits (edit_block), reading PDFs/Excel/large files
in parts, and fast search.

Only those tools are exposed. Its command/process tools would bypass the
shell rules, and set_config_value could switch off its own guards. It runs
with its own home folder under ~/.aethel, telemetry off and no remote flags."""
import json
import os
import shutil

from mcp import types as mt

from ..paths import aethel_home
from ..safety.changes import ChangeLog
from ..safety.permissions import Permissions
from .base import Assessment, Tool, ToolContext, ToolResult
from .local_fs import NEED_ABSOLUTE
from .mcp_hub import ServerSpec
from .paths import resolve_user_path

SERVER = "files"
PACKAGE = "@wonderwhy-er/desktop-commander@0.2.51"
HIDDEN_PARAMS = {"isUrl", "origin"}  # no fetching URLs; origin is its own UI's field

# remote name -> (local name, tier, path argument, path check)
EXPOSED = {
    "read_file": ("dc_read_file", "read", "path", "read"),
    "get_file_info": ("dc_file_info", "read", "path", "read"),
    "start_search": ("dc_search", "read", "path", "read"),
    "get_more_search_results": ("dc_search_more", "read", None, None),
    "stop_search": ("dc_search_stop", "read", None, None),
    "edit_block": ("dc_edit_block", "write", "file_path", "write"),
}


class FileCommander:
    def __init__(self, perms: Permissions | None, changes: ChangeLog | None):
        self.perms = perms
        self.changes = changes
        self.hub = None

    def _tool(self, remote: mt.Tool) -> Tool:
        name, tier, key, mode = EXPOSED[remote.name]
        schema = dict(remote.inputSchema or {"type": "object"})
        schema["properties"] = {k: v for k, v in (schema.get("properties") or {}).items() if k not in HIDDEN_PARAMS}
        if "required" in schema:
            schema["required"] = [r for r in schema["required"] if r not in HIDDEN_PARAMS]

        def assess(args: dict) -> Assessment:
            if key is None:
                return Assessment("allow", "", remote.name.replace("_", " "))
            raw = args.get(key)
            path = resolve_user_path(raw)
            if path is None:
                return Assessment("deny", NEED_ABSOLUTE, str(raw or "(no path)"))
            d = self.perms.check_path(str(path), mode)
            if d.verdict == "allow" and remote.name == "start_search" and args.get("searchType") == "content":
                # Reads every file under the folder, including ones the hard rules never let a task read.
                return Assessment("ask", "Searching inside files reads every file in that folder.",
                                  f"Search inside files in {path}")
            return Assessment(d.verdict, d.reason, str(path))

        def scope(args: dict) -> str:
            path = resolve_user_path(args.get(key)) if key else None
            return os.path.normcase(str(path.parent if mode == "write" else path)) if path is not None else name

        async def handler(args: dict, ctx: ToolContext) -> ToolResult:
            args = {k: v for k, v in args.items() if k not in HIDDEN_PARAMS}
            if key is not None:
                path = resolve_user_path(args.get(key))
                if path is None:
                    return ToolResult(False, NEED_ABSOLUTE)
                args[key] = str(path)  # exactly the path that was checked
                if mode == "write":
                    self.changes.record_before_write(str(path), ctx.task_id)
            return await self.hub.call(SERVER, remote.name, args)

        return Tool(name, remote.description or name, schema, tier, handler, assess, grant_scope=scope,
                    toolgroup="files")

    def adapt(self, hub, remote_tools: list[mt.Tool]) -> list[Tool]:
        self.hub = hub
        return [self._tool(t) for t in remote_tools if t.name in EXPOSED]


def file_commander_spec(fc: FileCommander) -> ServerSpec | None:
    """None when Node isn't installed."""
    npx = shutil.which("npx")
    if npx is None:
        return None
    home = aethel_home() / "desktop-commander"
    config = home / ".claude-server-commander" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    # Re-applied every start. An empty allow-list means "anywhere": Aethel's own
    # permission check (and the user's approvals) decide, before every call.
    config.write_text(json.dumps({**current, "telemetryEnabled": False, "allowedDirectories": []}), encoding="utf-8")
    return ServerSpec(SERVER, [npx, "-y", PACKAGE, "--no-onboarding"], fc.adapt, env={
        "USERPROFILE": str(home),                      # its config lives here, not in the user's home
        "DESKTOP_COMMANDER_DISABLE_TELEMETRY": "1",
        "DC_FLAG_URL": "http://127.0.0.1:9/flags.json",  # no remote feature flags
    })
