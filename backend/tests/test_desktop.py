import pytest
from mcp import types as mt

from aethel.tools import desktop
from aethel.tools.desktop import App, Desktop, parse_snapshot
from aethel.tools.registry import ToolRegistry

pytestmark = pytest.mark.anyio

SNAPSHOT = '''Active window: Inbox - Outlook
List of Interactive Elements:
window "Inbox - Outlook"
├── (120,40) button "New mail"  [action: click]
├── (900,610) button "Send"  [action: click]
└── (400,300) edit "Subject"  [action: type]  [focused]

window "Untitled - Notepad"
└── (50,60) menuitem "File"  [action: click]
'''


def _remote(name, props=("loc", "label")):
    schema = {"type": "object", "properties": {p: {"type": "string"} for p in props}, "required": ["label"]}
    return mt.Tool(name=name, description=f"{name} tool", inputSchema=schema)


ALL = ["App", "Snapshot", "Click", "Type", "Scroll", "Move", "Shortcut", "Wait", "WaitFor", "Clipboard",
       "MultiSelect", "MultiEdit", "DisplayInventory",
       "PowerShell", "FileSystem", "Registry", "Process", "Scrape", "Notification", "Screenshot"]


@pytest.fixture
def d(monkeypatch):
    apps = {"at": App(10, "outlook"), "fg": App(11, "notepad")}
    dk = Desktop(window_at=lambda x, y: apps["at"], foreground=lambda: apps["fg"])
    tools = {t.name: t for t in dk.adapt(hub=None, remote_tools=[_remote(n) for n in ALL])}
    dk.elements = parse_snapshot(SNAPSHOT)
    return dk, tools, apps


def test_parse_snapshot():
    els = parse_snapshot(SNAPSHOT)
    assert [(e.x, e.y, e.role, e.name, e.window) for e in els] == [
        (120, 40, "button", "New mail", "Inbox - Outlook"), (900, 610, "button", "Send", "Inbox - Outlook"),
        (400, 300, "edit", "Subject", "Inbox - Outlook"), (50, 60, "menuitem", "File", "Untitled - Notepad")]


def test_manifest_bypassing_tools_are_never_exposed(d):
    _, tools, _ = d
    assert set(tools) == {"win_app", "win_snapshot", "win_click", "win_type", "win_scroll", "win_move",
                          "win_shortcut", "win_wait", "win_wait_for", "win_clipboard", "win_multi_select",
                          "win_multi_edit", "win_displays"}
    assert "label" not in tools["win_click"].parameters["properties"]
    assert "label" not in tools["win_click"].parameters.get("required", [])
    assert all(t.group == "desktop" for t in tools.values())


def test_clicking_send_is_irreversible_and_scoped_to_the_app(d):
    _, tools, _ = d
    click = tools["win_click"]
    a = click.assess({"loc": [902, 612]})
    assert a.tier == "irreversible" and "Send" in a.target and "outlook" in a.target
    assert click.scope_for({"loc": [902, 612]}) == "outlook"
    plain = click.assess({"loc": "[120, 40]"})
    assert plain.verdict == "allow" and plain.tier is None and "New mail" in plain.target


def test_launching_shells_or_programs_asks(d):
    _, tools, _ = d
    app = tools["win_app"]
    assert app.assess({"mode": "launch", "name": "Notepad"}).verdict == "allow"
    assert app.scope_for({"mode": "launch", "name": "Notepad"}) == "notepad"
    for name in ["cmd", "Command Prompt", "PowerShell", "Windows Terminal", "regedit", "python"]:
        assert app.assess({"mode": "launch", "name": name}).verdict == "ask", name
    assert app.assess({"mode": "launch_executable", "name": r"C:\x\tool.exe"}).verdict == "ask"


def test_shortcuts(d):
    _, tools, _ = d
    sc = tools["win_shortcut"]
    assert sc.assess({"shortcut": "ctrl+s"}).verdict == "allow"
    assert sc.assess({"shortcut": "win+r"}).verdict == "ask"
    assert sc.assess({"shortcut": "Alt+F4"}).tier == "irreversible"
    assert sc.scope_for({"shortcut": "ctrl+s"}) == "notepad"  # the foreground app


def test_aethel_itself_and_system_prompts_are_off_limits(d, monkeypatch):
    _, tools, apps = d
    monkeypatch.setenv("AETHEL_PARENT_PID", "10")
    assert tools["win_click"].assess({"loc": [1, 1]}).verdict == "deny"  # the window under the point is ours
    apps["at"] = App(99, "consent")
    assert tools["win_click"].assess({"loc": [1, 1]}).verdict == "deny"
    apps["fg"] = App(98, "Aethel")
    assert tools["win_type"].assess({"text": "hi"}).verdict == "deny"     # typing goes to the foreground
    assert tools["win_shortcut"].assess({"shortcut": "enter"}).verdict == "deny"


async def test_snapshot_refreshes_the_element_map(d):
    dk, tools, _ = d

    class Hub:
        async def call(self, server, tool, args):
            from aethel.tools.base import ToolResult
            assert args.get("use_vision") is False
            return ToolResult(True, 'window "W"\n└── (5,5) button "Delete"  [action: click]', untrusted=True)

    dk.hub = Hub()
    result = await tools["win_snapshot"].handler({}, None)
    assert result.untrusted
    assert [e.name for e in dk.elements] == ["Delete"]
    assert tools["win_click"].assess({"loc": [5, 5]}).tier == "irreversible"


def test_spec_is_pinned_and_telemetry_is_off(monkeypatch):
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "C:/bin/uvx.exe")
    spec = desktop.desktop_spec(Desktop())
    assert spec.command == ["C:/bin/uvx.exe", "windows-mcp==0.8.6", "serve"]
    assert spec.env["ANONYMIZED_TELEMETRY"] == "false"
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    assert desktop.desktop_spec(Desktop()) is None


def test_window_lookup_works_on_this_machine():
    app = desktop.foreground_window()
    assert app is None or isinstance(app.name, str)
