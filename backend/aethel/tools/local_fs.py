import os

import anyio

from ..safety.changes import ChangeLog
from ..safety.permissions import Permissions
from .base import Assessment, Tool, ToolContext, ToolResult
from .paths import resolve_user_path

MAX_READ_CHARS = 50_000
MAX_LIST = 200
NEED_ABSOLUTE = "Use an absolute path (starting with a drive letter, ~ or %USERPROFILE%)."


def _path_arg(args: dict) -> str:
    value = args.get("path")
    return value if isinstance(value, str) else ""


def fs_tools(perms: Permissions, changes: ChangeLog) -> list[Tool]:
    def assess(mode):
        def run(args: dict) -> Assessment:
            raw = _path_arg(args)
            resolved = resolve_user_path(raw)
            if resolved is None:
                return Assessment("deny", NEED_ABSOLUTE, raw or "(no path)")
            d = perms.check_path(str(resolved), mode)
            return Assessment(d.verdict, d.reason, str(resolved))
        return run

    def write_scope(args: dict) -> str:
        # "Allow for this task" on a write covers the folder the file is in.
        p = resolve_user_path(_path_arg(args))
        return os.path.normcase(str(p.parent)) if p is not None else ""

    async def fs_list(args: dict, ctx: ToolContext) -> ToolResult:
        p = resolve_user_path(_path_arg(args))
        if p is None:
            return ToolResult(False, NEED_ABSOLUTE)
        if not p.is_dir():
            return ToolResult(False, f"Not a folder: {p}")
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))[:MAX_LIST]
        lines = [f"{'[dir] ' if e.is_dir() else ''}{e.name}" + ("" if e.is_dir() else f"  ({e.stat().st_size} bytes)")
                 for e in entries]
        return ToolResult(True, "\n".join(lines) or "(empty folder)", untrusted=True)

    async def fs_read(args: dict, ctx: ToolContext) -> ToolResult:
        p = resolve_user_path(_path_arg(args))
        if p is None:
            return ToolResult(False, NEED_ABSOLUTE)
        if not p.is_file():
            return ToolResult(False, f"No such file: {p}")
        text = await anyio.to_thread.run_sync(lambda: p.read_text(encoding="utf-8", errors="replace"))
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + f"\n[truncated: file has {len(text)} characters]"
        return ToolResult(True, text, untrusted=True)

    async def fs_write(args: dict, ctx: ToolContext) -> ToolResult:
        p = resolve_user_path(_path_arg(args))
        if p is None:
            return ToolResult(False, NEED_ABSOLUTE)
        content = args.get("content")
        if not isinstance(content, str):
            return ToolResult(False, "content must be a string.")
        append = args.get("mode") == "append"

        def write() -> None:
            changes.record_before_write(str(p), ctx.task_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a" if append else "w", encoding="utf-8") as f:
                f.write(content)

        await anyio.to_thread.run_sync(write)
        return ToolResult(True, f"{'Appended' if append else 'Wrote'} {len(content)} characters to {p}")

    path_only = {"type": "object", "properties": {"path": {"type": "string", "description": "Absolute path"}},
                 "required": ["path"]}
    return [
        Tool("fs_list", "List the files and folders inside a folder.", path_only, "read", fs_list, assess("read")),
        Tool("fs_read", "Read a text file.", path_only, "read", fs_read, assess("read")),
        Tool(
            "fs_write",
            "Create or overwrite a text file (mode 'append' adds to the end). Parent folders are created.",
            {"type": "object", "properties": {
                "path": {"type": "string", "description": "Absolute path"},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["overwrite", "append"]}},
             "required": ["path", "content"]},
            "write", fs_write, assess("write"), grant_scope=write_scope,
        ),
    ]
