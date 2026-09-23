import os
import subprocess
from pathlib import Path

import anyio

from ..safety.permissions import Permissions
from .base import Assessment, Tool, ToolContext, ToolResult

TIMEOUT_S = 60
MAX_OUTPUT = 8000


def shell_tool(perms: Permissions) -> Tool:
    def assess(args: dict) -> Assessment:
        command = args.get("command") if isinstance(args.get("command"), str) else ""
        d = perms.check_command(command)
        return Assessment(d.verdict, d.reason, command or "(no command)")

    async def run(args: dict, ctx: ToolContext) -> ToolResult:
        command = args["command"]
        argv = ["cmd", "/c", command] if os.name == "nt" else ["/bin/sh", "-c", command]

        def execute() -> subprocess.CompletedProcess:
            return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=str(Path.home()),
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)

        try:
            done = await anyio.to_thread.run_sync(execute)
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"Timed out after {TIMEOUT_S}s.")
        output = (done.stdout or "") + (("\n[stderr]\n" + done.stderr) if done.stderr else "")
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + "\n[truncated]"
        return ToolResult(done.returncode == 0, f"exit code {done.returncode}\n{output}".strip(), untrusted=True)

    return Tool(
        "shell_run",
        "Run a Windows command-line command (cmd.exe) in the user's home folder. No pipes or chaining.",
        {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        "write", run, assess,
    )
