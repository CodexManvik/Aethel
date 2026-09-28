"""Connects to MCP servers over stdio and exposes their tools (spec §4.2).

Each server's adapter decides which of its tools exist for the agent and how
risky each is; nothing is passed through unvetted. A cancelled call (task
cancel, kill switch) hard-restarts its server so no typing or clicking keeps
going after the user said stop."""
import asyncio
import contextlib
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import psutil
from mcp import ClientSession, StdioServerParameters
from mcp import types as mt
from mcp.client.stdio import get_default_environment, stdio_client

from ..paths import aethel_home
from .base import Assessor, RiskTier, Tool, ToolContext, ToolResult
from .registry import ToolRegistry

log = logging.getLogger("aethel.mcp")
CALL_TIMEOUT_S = 120
MAX_RESULT_CHARS = 20_000

Adapter = Callable[["McpHub", list[mt.Tool]], list[Tool]]


@dataclass
class ServerSpec:
    name: str
    command: list[str]
    adapt: Adapter
    env: dict[str, str] = field(default_factory=dict)  # on top of the SDK's minimal safe environment


@dataclass
class _Conn:
    spec: ServerSpec
    session: ClientSession | None = None
    pid: int | None = None
    tools: list[Tool] = field(default_factory=list)
    status: str = "starting"
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    runner: asyncio.Task | None = None


def _to_result(result: mt.CallToolResult) -> ToolResult:
    parts = []
    for item in result.content:
        if isinstance(item, mt.TextContent):
            parts.append(item.text)
        elif isinstance(item, mt.ImageContent):
            parts.append("[an image was returned]")
    text = "\n".join(parts).strip() or ("Error." if result.isError else "Done.")
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n[truncated]"
    return ToolResult(not result.isError, text, untrusted=True)


def _kill_tree(pid: int | None) -> None:
    if pid is None:
        return
    try:
        root = psutil.Process(pid)
        procs = root.children(recursive=True) + [root]
    except psutil.Error:
        return
    for p in procs:
        with contextlib.suppress(psutil.Error):
            p.kill()


def mcp_tool(hub: "McpHub", server: str, remote: mt.Tool, name: str, tier: RiskTier, assess: Assessor,
             description: str | None = None, **kw) -> Tool:
    """A Tool whose handler calls `remote` on `server`."""
    async def handler(args: dict, ctx: ToolContext) -> ToolResult:
        return await hub.call(server, remote.name, args)

    return Tool(name, description or remote.description or name, remote.inputSchema or {"type": "object"},
                tier, handler, assess, **kw)


class McpHub:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._conns: dict[str, _Conn] = {}
        self._spawn_lock = asyncio.Lock()  # one spawn at a time, so a new child pid is unambiguous
        self._background: set[asyncio.Task] = set()

    def start(self, specs: list[ServerSpec]) -> None:
        """Connect in the background; a slow first `uvx` download never blocks startup."""
        for spec in specs:
            self._launch(spec)

    async def wait_ready(self, server: str, timeout: float) -> bool:
        conn = self._conns.get(server)
        if conn is None:
            return False
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(conn.ready.wait(), timeout)
        return conn.status == "running"

    def status(self) -> dict[str, str]:
        return {name: conn.status for name, conn in self._conns.items()}

    async def call(self, server: str, tool: str, args: dict) -> ToolResult:
        conn = self._conns.get(server)
        if conn is None or conn.session is None:
            return ToolResult(False, f"The {server} tools aren't connected right now.")
        try:
            result = await asyncio.wait_for(conn.session.call_tool(tool, args), CALL_TIMEOUT_S)
        except asyncio.CancelledError:
            self._restart_soon(server)  # stop whatever it's still doing
            raise
        except asyncio.TimeoutError:
            self._restart_soon(server)
            return ToolResult(False, f"{tool} didn't finish within {CALL_TIMEOUT_S}s.")
        except Exception as exc:
            return ToolResult(False, f"Error: {exc}")
        return _to_result(result)

    async def restart(self, server: str) -> None:
        conn = self._conns.get(server)
        if conn is None or conn.stop.is_set():
            return  # unknown, or already restarting
        await self._shutdown(conn)
        self._launch(conn.spec)

    async def stop(self) -> None:
        for conn in list(self._conns.values()):
            await self._shutdown(conn, hard=False)

    # ---- internals ---------------------------------------------------------
    def _restart_soon(self, server: str) -> None:
        task = asyncio.get_running_loop().create_task(self.restart(server))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _launch(self, spec: ServerSpec) -> None:
        conn = _Conn(spec)
        self._conns[spec.name] = conn
        conn.runner = asyncio.get_running_loop().create_task(self._serve(conn))

    async def _shutdown(self, conn: _Conn, hard: bool = True) -> None:
        """hard: kill now (the SDK would give a server 2s to finish typing);
        otherwise let it exit on closed stdin like the MCP spec asks."""
        conn.stop.set()
        if hard:
            _kill_tree(conn.pid)
        if conn.runner is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await conn.runner
        conn.status = "stopped"

    async def _serve(self, conn: _Conn) -> None:
        spec = conn.spec
        logs = aethel_home() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        params = StdioServerParameters(command=spec.command[0], args=spec.command[1:],
                                       env={**get_default_environment(), **spec.env})
        try:
            with open(logs / f"mcp-{spec.name}.log", "a", encoding="utf-8") as errlog:
                async with AsyncExitStack() as stack:
                    async with self._spawn_lock:
                        before = {p.pid for p in psutil.Process().children()}
                        read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
                        new = [p.pid for p in psutil.Process().children() if p.pid not in before]
                        conn.pid = new[0] if new else None
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await asyncio.wait_for(session.initialize(), 60)
                    listed = (await session.list_tools()).tools
                    conn.tools = spec.adapt(self, listed)
                    for tool in conn.tools:
                        self.registry.register(tool)
                    conn.session = session
                    conn.status = "running"
                    conn.ready.set()
                    await conn.stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # an anyio ExceptionGroup can wrap anything
            if not conn.stop.is_set():
                log.warning("MCP server %s failed: %r", spec.name, exc)
                conn.status = f"failed: {exc}"[:200]
        finally:
            conn.session = None
            for tool in conn.tools:
                self.registry.unregister(tool.name)
            conn.tools = []
            conn.ready.set()  # wake wait_ready() whatever happened
