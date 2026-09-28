import pytest
import yaml
from mcp import types as mt

from aethel.paths import db_path
from aethel.safety.changes import ChangeLog
from aethel.safety.permissions import Permissions, default_manifest
from aethel.store.db import Database
from aethel.tools import file_commander as fc
from aethel.tools.base import ToolContext, ToolResult

pytestmark = pytest.mark.anyio
REMOTE = ["read_file", "edit_block", "start_search", "get_more_search_results", "stop_search", "get_file_info",
          "start_process", "interact_with_process", "set_config_value", "write_file", "move_file", "kill_process",
          "list_directory", "give_feedback_to_desktop_commander"]


class FakeHub:
    def __init__(self, changes):
        self.calls, self.changes = [], changes

    async def call(self, server, tool, args):
        self.calls.append((tool, args, len(self.changes.for_task("t1"))))
        return ToolResult(True, "ok", untrusted=True)


@pytest.fixture
def env(tmp_path):
    db = Database(db_path())
    m = default_manifest()
    m["filesystem"]["allowed_read_paths"] = [str(tmp_path)]
    m["filesystem"]["allowed_write_paths"] = [str(tmp_path / "out")]
    m["filesystem"]["forbidden_paths"] = [str(tmp_path / "secret")]
    (tmp_path / "p.yaml").write_text(yaml.safe_dump(m), encoding="utf-8")
    changes = ChangeLog(db)
    hub = FakeHub(changes)
    remote = [mt.Tool(name=n, description=n, inputSchema={"type": "object", "properties": {
        "path": {"type": "string"}, "isUrl": {"type": "boolean"}, "origin": {"type": "string"}}}) for n in REMOTE]
    tools = {t.name: t for t in fc.FileCommander(Permissions(tmp_path / "p.yaml"), changes).adapt(hub, remote)}
    yield tools, hub, tmp_path
    db.close()


def test_only_the_vetted_tools_exist(env):
    tools, _, _ = env
    assert set(tools) == {"dc_read_file", "dc_edit_block", "dc_search", "dc_search_more", "dc_search_stop",
                          "dc_file_info"}
    assert "isUrl" not in tools["dc_read_file"].parameters["properties"]
    assert tools["dc_edit_block"].tier == "write" and tools["dc_read_file"].tier == "read"


def test_reads_are_path_checked_and_urls_refused(env):
    tools, _, tmp = env
    read = tools["dc_read_file"]
    assert read.assess({"path": str(tmp / "a.pdf")}).verdict == "allow"
    assert read.assess({"path": str(tmp / "secret" / "k")}).verdict == "deny"
    assert read.assess({"path": "https://example.com/x"}).verdict == "deny"
    assert read.assess({"path": str(tmp / ".env")}).verdict == "deny"


def test_content_search_asks_because_it_reads_every_file(env):
    tools, _, tmp = env
    search = tools["dc_search"]
    assert search.assess({"path": str(tmp), "pattern": "x", "searchType": "files"}).verdict == "allow"
    assert search.assess({"path": str(tmp), "pattern": "x", "searchType": "content"}).verdict == "ask"
    assert search.assess({"path": str(tmp / "secret"), "pattern": "x"}).verdict == "deny"


async def test_edit_block_is_checked_and_snapshotted_first(env):
    tools, hub, tmp = env
    edit = tools["dc_edit_block"]
    target = tmp / "out" / "notes.txt"
    target.parent.mkdir()
    target.write_text("old", encoding="utf-8")
    assert edit.assess({"file_path": str(tmp / "elsewhere.txt")}).verdict == "ask"
    assert edit.assess({"file_path": str(target)}).verdict == "allow"
    await edit.handler({"file_path": str(target), "old_string": "old", "new_string": "new"}, ToolContext("t1"))
    tool, args, recorded = hub.calls[-1]
    assert (tool, args["file_path"], recorded) == ("edit_block", str(target), 1)


def test_spec_isolates_config_and_turns_telemetry_off(monkeypatch, aethel_home):
    monkeypatch.setattr(fc.shutil, "which", lambda name: "C:/node/npx.cmd")
    spec = fc.file_commander_spec(fc.FileCommander(None, None))
    assert spec.command == ["C:/node/npx.cmd", "-y", "@wonderwhy-er/desktop-commander@0.2.51", "--no-onboarding"]
    assert spec.env["DESKTOP_COMMANDER_DISABLE_TELEMETRY"] == "1" and spec.env["DC_FLAG_URL"].startswith("http://127.")
    config = yaml.safe_load((aethel_home / "desktop-commander" / ".claude-server-commander" / "config.json")
                            .read_text(encoding="utf-8"))
    assert config["telemetryEnabled"] is False and config["allowedDirectories"] == []
    assert spec.env["USERPROFILE"] == str(aethel_home / "desktop-commander")
    monkeypatch.setattr(fc.shutil, "which", lambda name: None)
    assert fc.file_commander_spec(fc.FileCommander(None, None)) is None
