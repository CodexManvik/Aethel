import os
import sys

import pytest
import yaml

from aethel.paths import db_path
from aethel.safety.changes import ChangeLog
from aethel.safety.permissions import Permissions, default_manifest
from aethel.store.db import Database
from aethel.tools.base import ToolContext, ToolResult
from aethel.tools.mcp_hub import McpHub
from aethel.tools.office import OFFICE_TOOLS, Office, office_spec
from aethel.tools.registry import ToolRegistry

pytestmark = pytest.mark.anyio


class FakeHub:
    def __init__(self, changes):
        self.calls, self.changes = [], changes

    async def call(self, server, tool, args):
        self.calls.append((tool, args, len(self.changes.for_task("t1"))))
        return ToolResult(True, "done", untrusted=True)


@pytest.fixture
def o(tmp_path):
    db = Database(db_path())
    manifest = default_manifest()
    manifest["filesystem"]["allowed_read_paths"] = [str(tmp_path)]
    manifest["filesystem"]["allowed_write_paths"] = [str(tmp_path / "out")]
    manifest["filesystem"]["forbidden_paths"] = [str(tmp_path / "secret")]
    (tmp_path / "p.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    changes = ChangeLog(db)
    office = Office(Permissions(tmp_path / "p.yaml"), changes)
    hub = FakeHub(changes)

    from mcp import types as mt
    remote = [mt.Tool(name=n, description=n, inputSchema={"type": "object"}) for n in [*OFFICE_TOOLS, "extra"]]
    tools = {t.name: t for t in office.adapt(hub, remote)}
    yield tools, hub, tmp_path
    db.close()


async def test_saves_are_checked_and_recorded_before_they_happen(o):
    tools, hub, tmp = o
    save = tools["word_save"]
    assert save.assess({"path": str(tmp / "secret" / "a.docx")}).verdict == "deny"
    assert save.assess({"path": "a.docx"}).verdict == "deny"           # relative
    assert save.assess({"path": str(tmp / "elsewhere.docx")}).verdict == "ask"
    target = tmp / "out" / "essay.docx"
    assert save.assess({"path": str(target)}).verdict == "allow"
    result = await save.handler({"path": str(target)}, ToolContext(task_id="t1"))
    assert result.ok
    tool, args, changes_before_call = hub.calls[-1]
    assert (tool, args["path"], changes_before_call) == ("word_save", str(target), 1)


async def test_opening_a_file_is_a_read(o):
    tools, _, tmp = o
    assert tools["word_open"].tier == "read"
    assert tools["word_open"].assess({"path": str(tmp / "x.docx")}).verdict == "allow"
    assert tools["word_open"].assess({"path": str(tmp / "secret" / "x.docx")}).verdict == "deny"
    assert tools["excel_open"].assess({}).verdict == "allow"  # a new workbook


def test_only_known_tools_and_their_groups(o):
    tools, _, _ = o
    assert set(tools) == set(OFFICE_TOOLS)
    assert tools["word_type"].tier == "write" and tools["word_read"].tier == "read"
    assert tools["word_type"].scope_for({"text": "x"}) == "word"
    assert all(t.group == "office" for t in tools.values())


@pytest.mark.skipif(os.name != "nt", reason="COM")
async def test_the_real_server_lists_its_tools():
    registry = ToolRegistry()
    hub = McpHub(registry)
    spec = office_spec(Office(None, None))
    assert spec.command[0] == sys.executable
    hub.start([spec])
    try:
        assert await hub.wait_ready("office", 60)
        assert sorted(registry.names()) == sorted(OFFICE_TOOLS)
    finally:
        await hub.stop()
