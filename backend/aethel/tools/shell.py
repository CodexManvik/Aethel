import asyncio
import contextlib
import locale
import os
import signal
import subprocess
from pathlib import Path

from ..safety.permissions import Permissions
from .base import Assessment, Tool, ToolContext, ToolResult

TIMEOUT_S = 60
MAX_OUTPUT = 8000
_SECRET_NAMES = {"AETHEL_TOKEN", "AETHEL_PARENT_PID"}
_SECRET_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET")


def scrubbed_env() -> dict[str, str]:
    """The backend's environment minus its own token and anything that looks
    like a credential, so a command can't echo them back to the model."""
    return {k: v for k, v in os.environ.items()
            if k.upper() not in _SECRET_NAMES and not k.upper().endswith(_SECRET_SUFFIXES)}


def normalise_command(command) -> str:
    """"Allow for this task" on a command covers exactly that command."""
    return " ".join(command.lower().split()) if isinstance(command, str) else ""


def _decode(data: bytes) -> str:
    return data.decode(locale.getpreferredencoding(False), errors="replace")


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill the command and everything it started. Killing only cmd.exe would
    leave a grandchild holding the output pipes open."""
    if proc.returncode is None:
        try:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/T", "/F", "/PID", str(proc.pid), stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                await killer.wait()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass  # already gone
    try:
        await asyncio.wait_for(proc.wait(), 5)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def shell_tool(perms: Permissions) -> Tool:
    def assess(args: dict) -> Assessment:
        command = args.get("command") if isinstance(args.get("command"), str) else ""
        d = perms.check_command(command)
        return Assessment(d.verdict, d.reason, command or "(no command)")

    async def run(args: dict, ctx: ToolContext) -> ToolResult:
        command = args["command"]
        argv = ["cmd", "/c", command] if os.name == "nt" else ["/bin/sh", "-c", command]
        extra: dict = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
                       if os.name == "nt" else {"start_new_session": True})
        spawn = asyncio.ensure_future(asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=str(Path.home()), env=scrubbed_env(), **extra))
        try:
            proc = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            # Cancelled while the pipes were still being set up: the command
            # may already be running, so let the spawn finish and kill its tree
            # (asyncio would otherwise kill only cmd.exe, orphaning the rest).
            with contextlib.suppress(Exception):
                await _kill_tree(await spawn)
            raise
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
        except asyncio.TimeoutError:
            await asyncio.shield(_kill_tree(proc))
            return ToolResult(False, f"Timed out after {TIMEOUT_S}s.")
        except asyncio.CancelledError:
            await asyncio.shield(_kill_tree(proc))
            raise
        output = _decode(stdout) + (("\n[stderr]\n" + _decode(stderr)) if stderr else "")
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + "\n[truncated]"
        return ToolResult(proc.returncode == 0, f"exit code {proc.returncode}\n{output}".strip(), untrusted=True)

    return Tool(
        "shell_run",
        "Run a Windows command-line command (cmd.exe) in the user's home folder. No pipes or chaining.",
        {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        "write", run, assess, grant_scope=lambda args: normalise_command(args.get("command")),
    )
