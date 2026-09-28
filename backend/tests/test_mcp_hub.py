import asyncio
import sys
from pathlib import Path

import pytest

from aethel.tools.base import Assessment, ToolContext
from aethel.tools.mcp_hub import McpHub, ServerSpec, mcp_tool
from aethel.tools.registry import ToolRegistry

pytestmark = pytest.mark.anyio
SERVER = str(Path(__file__).with_name("fake_mcp_server.py"))
EXPOSED = {"echo": "read", "fail": "read", "slow": "write", "pid": "read"}


def adapt(hub, remote_tools):
    return [mcp_tool(hub, "fake", t, f"fake_{t.name}", EXPOSED[t.name], lambda a: Assessment("allow", "", "x"))
            for t in remote_tools if t.name in EXPOSED]


@pytest.fixture
async def hub():
    registry = ToolRegistry()
    hub = McpHub(registry)
    hub.start([ServerSpec("fake", [sys.executable, SERVER], adapt)])
    assert await hub.wait_ready("fake", 30)
    yield hub
    await hub.stop()


async def _call(hub, name, **args):
    return await hub.registry.get(name).handler(args, ToolContext(task_id=None))


async def test_only_adapted_tools_are_registered(hub):
    assert sorted(hub.registry.names()) == ["fake_echo", "fake_fail", "fake_pid", "fake_slow"]
    assert hub.registry.get("fake_echo").parameters["properties"]["text"]["type"] == "string"
    assert hub.status() == {"fake": "running"}


async def test_results_are_untrusted_and_errors_are_not_ok(hub):
    ok = await _call(hub, "fake_echo", text="hello")
    assert (ok.ok, ok.content, ok.untrusted) == (True, "hello", True)
    bad = await _call(hub, "fake_fail")
    assert not bad.ok and "nope" in bad.content


async def test_a_cancelled_call_restarts_the_server(hub):
    before = (await _call(hub, "fake_pid")).content
    call = asyncio.ensure_future(_call(hub, "fake_slow"))
    await asyncio.sleep(0.5)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    for _ in range(300):
        await asyncio.sleep(0.1)
        if hub.status()["fake"] == "running" and hub.registry.get("fake_pid"):
            break
    after = (await _call(hub, "fake_pid")).content
    assert after != before


async def test_stop_unregisters_everything(hub):
    await hub.stop()
    assert hub.registry.names() == []
    result = await hub.call("fake", "echo", {"text": "x"})
    assert not result.ok


async def test_a_server_that_cannot_start_is_reported_not_fatal():
    hub = McpHub(ToolRegistry())
    hub.start([ServerSpec("ghost", ["definitely-not-a-program-xyz"], adapt)])
    assert not await hub.wait_ready("ghost", 10)
    assert hub.status()["ghost"].startswith("failed")
    await hub.stop()
