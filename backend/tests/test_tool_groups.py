import pytest
import yaml
from mcp import types as mt

from aethel.paths import db_path
from aethel.safety.changes import ChangeLog
from aethel.safety.permissions import Permissions, default_manifest
from aethel.store.db import Database
from aethel.tools import desktop, file_commander, office
from aethel.tools.base import Assessment, Tool, ToolResult
from aethel.tools.groups import CORE, GROUPS, USE_TOOLS, catalogue_line, use_tools_spec
from aethel.tools.launcher import open_url_tool
from aethel.tools.local_fs import fs_tools
from aethel.tools.registry import ToolRegistry
from aethel.tools.shell import shell_tool


def _tool(name, group="core"):
    async def run(args, ctx):
        return ToolResult(True, "ok")

    return Tool(name, f"{name}.", {"type": "object"}, "read", run, lambda a: Assessment("allow", "", name),
                toolgroup=group)


def test_a_tool_is_core_unless_it_says_otherwise():
    assert _tool("x").toolgroup == "core" and _tool("x", "office").toolgroup == "office"
    assert CORE == {"core"}


def test_catalogue_lists_only_groups_that_exist_and_are_not_active():
    both = catalogue_line({"core", "office", "files"}, {"core"})
    assert both.startswith("More tools on request (call use_tools): ")
    assert f"office ({GROUPS['office']})" in both and f"files ({GROUPS['files']})" in both
    assert "core" not in both.split(": ", 1)[1]
    assert "office" not in catalogue_line({"core", "office", "files"}, {"core", "office"})
    assert catalogue_line({"core", "office"}, {"core", "office"}) == ""      # nothing left to ask for
    assert catalogue_line({"core"}, {"core"}) == ""
    assert "browser" not in catalogue_line({"core", "office"}, {"core"})      # not registered: not offered
    assert catalogue_line({"core", "mystery"}, {"core"}).endswith("mystery")  # a group with no blurb still shows


def test_use_tools_names_only_the_groups_you_can_still_ask_for():
    spec = use_tools_spec(["office", "files"])
    assert spec.name == "use_tools" and spec.parameters["required"] == ["group"]
    assert spec.parameters["properties"]["group"]["enum"] == ["office", "files"]
    assert USE_TOOLS.parameters["properties"]["group"]["enum"] == list(GROUPS)


def test_registry_offers_tools_by_group_in_a_stable_order():
    reg = ToolRegistry()
    for name, group in [("fs_read", "core"), ("word_new", "office"), ("win_click", "core"), ("dc_search", "files")]:
        reg.register(_tool(name, group))
    assert [s.name for s in reg.specs({"core"})] == ["fs_read", "win_click"]
    assert [s.name for s in reg.specs({"core", "office"})] == ["fs_read", "word_new", "win_click"]  # registry order
    assert [s.name for s in reg.specs()] == ["fs_read", "word_new", "win_click", "dc_search"]       # None: all
    assert [s.name for s in reg.specs(set())] == []
    assert reg.groups() == {"core", "office", "files"}
    assert reg.names("office") == ["word_new"] and reg.names() == ["fs_read", "word_new", "win_click", "dc_search"]
    reg.unregister("dc_search")
    assert reg.groups() == {"core", "office"}  # reflects what is registered right now
    assert reg.specs({"core"}, compact=True)[0].name == "fs_read"


def test_every_builtin_tool_belongs_to_its_planned_group(tmp_path):
    db = Database(db_path())
    manifest = default_manifest()
    (tmp_path / "p.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    perms, changes = Permissions(tmp_path / "p.yaml"), ChangeLog(db)

    def remote(names):
        return [mt.Tool(name=n, description=n, inputSchema={"type": "object", "properties": {}}) for n in names]

    win = {t.name: t.toolgroup for t in desktop.Desktop(vision=object()).adapt(
        None, remote([*desktop.EXPOSED, "Screenshot"]))}
    assert {n for n, g in win.items() if g == "core"} == {
        "win_app", "win_snapshot", "win_click", "win_type", "win_scroll", "win_shortcut", "win_wait",
        "win_wait_for", "win_clipboard", "win_locate"}
    assert {n for n, g in win.items() if g == "desktop_extra"} == {
        "win_move", "win_multi_select", "win_multi_edit", "win_displays"}
    word = office.Office(perms, changes).adapt(None, remote(office.OFFICE_TOOLS))
    assert word and {t.toolgroup for t in word} == {"office"}
    dc = file_commander.FileCommander(perms, changes).adapt(None, remote(file_commander.EXPOSED))
    assert len(dc) == len(file_commander.EXPOSED) and {t.toolgroup for t in dc} == {"files"}
    own = [*fs_tools(perms, changes), shell_tool(perms), open_url_tool()]
    assert own and {t.toolgroup for t in own} == {"core"}
    db.close()
