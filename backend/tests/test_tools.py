import os

import pytest
import yaml

from aethel.paths import db_path
from aethel.safety.changes import ChangeLog
from aethel.safety.permissions import Permissions, default_manifest
from aethel.store.db import Database
from aethel.tools.base import Assessment, Tool, ToolContext, ToolResult
from aethel.tools.local_fs import MAX_READ_CHARS, fs_tools
from aethel.tools.registry import ToolRegistry
from aethel.tools.shell import shell_tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def env(tmp_path):
    manifest = default_manifest()
    manifest["filesystem"]["allowed_read_paths"] = [str(tmp_path)]
    manifest["filesystem"]["allowed_write_paths"] = [str(tmp_path / "out")]
    manifest["filesystem"]["forbidden_paths"] = [str(tmp_path / "secret")]
    (tmp_path / "permissions.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    db = Database(db_path())
    perms = Permissions(tmp_path / "permissions.yaml")
    changes = ChangeLog(db)
    tools = {t.name: t for t in fs_tools(perms, changes)}
    tools["shell_run"] = shell_tool(perms)
    yield tmp_path, tools, changes
    db.close()


def test_registry_rejects_bad_and_duplicate_names():
    async def h(a, c):
        return ToolResult(True, "")

    reg = ToolRegistry()
    tool = Tool("fs_read", "d", {"type": "object"}, "read", h, lambda a: Assessment("allow", "", ""))
    reg.register(tool)
    with pytest.raises(ValueError):
        reg.register(tool)
    with pytest.raises(ValueError):
        reg.register(Tool("fs.read", "d", {}, "read", h, lambda a: Assessment("allow", "", "")))
    assert reg.names() == ["fs_read"] and reg.specs()[0].name == "fs_read"


async def test_fs_write_then_read_and_list(env):
    tmp, tools, changes = env
    target = str(tmp / "out" / "poem.txt")
    assert tools["fs_write"].assess({"path": target, "content": "x"}).verdict == "allow"
    res = await tools["fs_write"].handler({"path": target, "content": "rain on the roof"}, ToolContext("t1"))
    assert res.ok and "16 characters" in res.content
    assert [c["path"] for c in changes.for_task("t1")] == [target]
    read = await tools["fs_read"].handler({"path": target}, ToolContext("t1"))
    assert read.ok and read.untrusted and read.content == "rain on the roof"
    listing = await tools["fs_list"].handler({"path": str(tmp / "out")}, ToolContext("t1"))
    assert "poem.txt" in listing.content
    appended = await tools["fs_write"].handler({"path": target, "content": "!", "mode": "append"}, ToolContext("t1"))
    assert appended.ok and open(target, encoding="utf-8").read() == "rain on the roof!"


async def test_fs_assessments(env):
    tmp, tools, _ = env
    assert tools["fs_write"].assess({"path": str(tmp / "elsewhere.txt"), "content": ""}).verdict == "ask"
    assert tools["fs_read"].assess({"path": str(tmp / "secret" / "k")}).verdict == "deny"
    assert tools["fs_read"].assess({}).verdict == "deny"


async def test_fs_read_truncates_and_reports_missing(env):
    tmp, tools, _ = env
    big = tmp / "big.txt"
    big.write_text("a" * (MAX_READ_CHARS + 10), encoding="utf-8")
    res = await tools["fs_read"].handler({"path": str(big)}, ToolContext(None))
    assert res.ok and "[truncated" in res.content
    missing = await tools["fs_read"].handler({"path": str(tmp / "nope.txt")}, ToolContext(None))
    assert not missing.ok


async def test_shell_run(env):
    _, tools, _ = env
    assert tools["shell_run"].assess({"command": "echo hi"}).verdict == "allow"
    assert tools["shell_run"].assess({"command": "echo hi > x"}).verdict == "deny"
    res = await tools["shell_run"].handler({"command": "echo hello"}, ToolContext(None))
    assert res.ok and "hello" in res.content and res.untrusted


async def test_shell_run_does_not_pass_secrets_to_the_command(env, monkeypatch):
    _, tools, _ = env
    monkeypatch.setenv("AETHEL_TOKEN", "tok-sekrit-1")
    monkeypatch.setenv("AETHEL_PARENT_PID", "4242")
    monkeypatch.setenv("GROQ_API_KEY", "key-sekrit-2")
    monkeypatch.setenv("SOME_Secret", "sec-sekrit-3")
    monkeypatch.setenv("AETHEL_TEST_VISIBLE", "visible-4")
    res = await tools["shell_run"].handler(
        {"command": "echo %AETHEL_TOKEN% %AETHEL_PARENT_PID% %GROQ_API_KEY% %SOME_Secret% %AETHEL_TEST_VISIBLE%"},
        ToolContext(None))
    assert "visible-4" in res.content
    for secret in ("tok-sekrit-1", "4242", "key-sekrit-2", "sec-sekrit-3"):
        assert secret not in res.content


def _ping_pids() -> set[str]:
    import subprocess
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq PING.EXE", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return {line.split('","')[1] for line in out.splitlines() if line.lower().startswith('"ping.exe"')}


@pytest.mark.skipif(os.name != "nt", reason="uses cmd.exe and ping")
async def test_shell_timeout_kills_the_whole_tree(env, monkeypatch):
    import time

    import aethel.tools.shell as shell
    _, tools, _ = env
    monkeypatch.setattr(shell, "TIMEOUT_S", 1)
    before = _ping_pids()
    t0 = time.monotonic()
    res = await tools["shell_run"].handler({"command": "ping -n 30 127.0.0.1"}, ToolContext(None))
    assert time.monotonic() - t0 < 3.5
    assert not res.ok and "Timed out" in res.content
    assert _ping_pids() - before == set()


@pytest.mark.skipif(os.name != "nt", reason="uses cmd.exe and ping")
async def test_shell_cancel_kills_the_command_promptly(env):
    import asyncio
    import time
    _, tools, _ = env
    before = _ping_pids()
    task = asyncio.ensure_future(tools["shell_run"].handler({"command": "ping -n 30 127.0.0.1"}, ToolContext(None)))
    for _ in range(100):  # wait until ping is actually running
        await asyncio.sleep(0.05)
        if _ping_pids() - before:
            break
    assert _ping_pids() - before, "ping never started"
    t0 = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - t0 < 2.0
    assert _ping_pids() - before == set()



async def test_fs_tools_resolve_home_and_reject_relative_paths(env, monkeypatch):
    from aethel.runtime.checks import Check, run_check
    tmp, tools, changes = env
    home = tmp / "out" / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    (tmp / "cwd").mkdir()
    monkeypatch.chdir(tmp / "cwd")
    expected = os.path.realpath(home / "poem.txt")

    verdict = tools["fs_write"].assess({"path": "~/poem.txt", "content": "x"})
    assert (verdict.verdict, verdict.target) == ("allow", expected)
    res = await tools["fs_write"].handler({"path": "~/poem.txt", "content": "rain"}, ToolContext("t4"))
    assert res.ok and expected in res.content
    assert open(expected, encoding="utf-8").read() == "rain"
    assert not (tmp / "cwd" / "~").exists()
    assert [c["path"] for c in changes.for_task("t4")] == [expected]
    assert run_check(Check(kind="file_exists", path="~/poem.txt")).passed
    assert run_check(Check(kind="file_contains", path="%USERPROFILE%/poem.txt", text="rain")).passed

    read = await tools["fs_read"].handler({"path": "~/poem.txt"}, ToolContext("t4"))
    assert read.ok and read.content == "rain"
    listing = await tools["fs_list"].handler({"path": "~"}, ToolContext("t4"))
    assert "poem.txt" in listing.content

    for name in ("fs_write", "fs_read", "fs_list"):
        relative = tools[name].assess({"path": "notes.txt", "content": "x"})
        assert relative.verdict == "deny" and "absolute" in relative.reason
    assert not (await tools["fs_write"].handler({"path": "notes.txt", "content": "x"}, ToolContext("t4"))).ok
    assert not (tmp / "cwd" / "notes.txt").exists()


def test_grant_scopes(env):
    tmp, tools, _ = env
    write = tools["fs_write"].scope_for
    assert write({"path": str(tmp / "d" / "a.txt")}) == write({"path": str(tmp / "d" / "b.txt")})
    assert write({"path": str(tmp / "d" / "a.txt")}) != write({"path": str(tmp / "e" / "c.txt")})
    assert tools["shell_run"].scope_for({"command": "  Winget   install foo "}) == "winget install foo"
    assert tools["shell_run"].scope_for({"command": "winget install bar"}) != "winget install foo"
    assert tools["fs_read"].scope_for({"path": str(tmp / "x")}) == "fs_read"  # no grant_scope: the whole tool


def test_registry_unregister():
    from aethel.tools.base import Assessment, Tool, ToolResult
    from aethel.tools.registry import ToolRegistry

    async def run(args, ctx):
        return ToolResult(True, "")

    reg = ToolRegistry()
    reg.register(Tool("win_click", "", {}, "write", run, lambda a: Assessment("allow", "", "")))
    reg.unregister("win_click")
    reg.unregister("never_there")  # no error
    assert reg.get("win_click") is None and reg.specs() == []


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
async def test_shell_cancel_kills_grandchildren_whose_parent_already_exited(env):
    """`start /b` lets cmd.exe exit at once, orphaning ping: taskkill /T on
    cmd's pid can't find it any more, the per-command job still can."""
    import asyncio
    _, tools, _ = env
    before = _ping_pids()
    task = asyncio.ensure_future(tools["shell_run"].handler({"command": "start /b ping -n 30 127.0.0.1"},
                                                            ToolContext(None)))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if _ping_pids() - before:
            break
    assert _ping_pids() - before, "ping never started"
    await asyncio.sleep(0.3)  # cmd.exe has exited by now
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    assert _ping_pids() - before == set()
