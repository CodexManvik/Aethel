"""The background browser: Playwright MCP behind a vetted adapter (Phase 3 spec §7). The sample texts below are what
@playwright/mcp 0.0.83 really returned in the probe (see the tools/browser.py docstring)."""
import base64
import io

import pytest
from mcp import types as mt

from aethel.tools import browser as br
from aethel.tools.base import ToolContext

pytestmark = pytest.mark.anyio

SNAPSHOT = '''- generic [active] [ref=e1]:
  - heading "Sign in to Probe" [level=1] [ref=e2]
  - generic [ref=e3]:
    - generic [ref=e4]:
      - text: Search
      - textbox "Search" [ref=e5]
    - generic [ref=e6]:
      - text: Password
      - textbox "Password" [ref=e7]
    - generic [ref=e8]:
      - text: Enter the 6-digit code
      - textbox "Enter the 6-digit code" [ref=e9]
    - textbox "Your secret" [ref=e14]
    - combobox "Colour" [ref=e10]:
      - option "Red" [selected]
      - option "Green"
    - button "Search" [ref=e11]
    - button "Delete account" [ref=e12]
  - link "Go to other page" [ref=e13] [cursor=pointer]:
    - /url: /other.html
  - paragraph [ref=e15]: Some words with "quotes" in them
  - button "Pay \\"now\\"" [ref=f4e20]
'''
PAGE = """### Page
- Page URL: http://127.0.0.1:8799/page.html
- Page Title: Probe form
- Console: 1 errors, 0 warnings
### Snapshot
```yaml
""" + SNAPSHOT + "```"
CODE = "### Ran Playwright code\n```js\nawait page.goto('http://127.0.0.1:8799/page.html');\n```\n"
ALL_REMOTE = [
    "browser_close", "browser_resize", "browser_console_messages", "browser_handle_dialog", "browser_emulate_media",
    "browser_evaluate", "browser_file_upload", "browser_drop", "browser_find", "browser_fill_form", "browser_press_key",
    "browser_type", "browser_navigate", "browser_navigate_back", "browser_network_requests", "browser_network_request",
    "browser_run_code_unsafe", "browser_take_screenshot", "browser_snapshot", "browser_click", "browser_drag",
    "browser_hover", "browser_select_option", "browser_tabs", "browser_wait_for"]


def png(width=1200, height=800) -> str:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 60)).save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode()


class FakeHub:
    """Answers each remote tool with scripted text (the adapter uses call_raw: McpHub.call would cut a long
    snapshot at 20,000 characters before the adapter could read it) and serves the internal evaluate and
    screenshot calls."""

    def __init__(self, replies=None, credential=None):
        self.replies = replies or {}
        self.calls = []
        self.credential = credential or {}   # ref -> "true"/"false"/None (the evaluate answer; None: it errors)

    async def call_raw(self, server, tool, args):
        self.calls.append((tool, dict(args)))
        if tool == "browser_evaluate":
            answer = self.credential.get(args["target"], "false")
            if answer is None:
                return mt.CallToolResult(content=[mt.TextContent(type="text", text="### Error\nRef not found")], isError=True)
            return mt.CallToolResult(content=[mt.TextContent(type="text", text=f"### Result\n{answer}\n### Ran Playwright code")])
        if tool == "browser_take_screenshot":
            return mt.CallToolResult(content=[mt.TextContent(type="text", text="### Result"),
                                              mt.ImageContent(type="image", data=png(), mimeType="image/png")])
        reply = self.replies.get(tool, "### Result\nDone")
        return mt.CallToolResult(content=[mt.TextContent(type="text", text=reply)], isError=reply.startswith("### Error"))


def remote_tools():
    return [mt.Tool(name=n, description=n, inputSchema={"type": "object", "properties": {
        "target": {"type": "string"}, "element": {"type": "string"}, "text": {"type": "string"},
        "url": {"type": "string"}, "key": {"type": "string"}}}) for n in ALL_REMOTE]


@pytest.fixture
def b(tmp_path):
    browser = br.Browser(thumbnails=lambda: False, out_dir=tmp_path / "out")
    (tmp_path / "out").mkdir()
    hub = FakeHub({"browser_snapshot": PAGE})
    tools = {t.name: t for t in browser.adapt(hub, remote_tools())}
    return browser, tools, hub, tmp_path


async def run(tool, **args):
    return await tool.handler(args, ToolContext(task_id="t1"))


async def see_the_page(tools):
    """The model has taken a snapshot, so the browser knows the page's elements."""
    return await run(tools["browser_snapshot"])


# ---- what exists ----------------------------------------------------------------------------------------------
def test_only_the_vetted_tools_exist_and_the_dangerous_ones_never_do(b):
    _, tools, _, _ = b
    assert set(tools) == set(br.EXPOSED)
    for banned in ("browser_evaluate", "browser_run_code_unsafe", "browser_file_upload", "browser_take_screenshot",
                   "browser_console_messages", "browser_network_requests", "browser_network_request", "browser_find",
                   "browser_drag", "browser_drop", "browser_resize", "browser_emulate_media"):
        assert banned not in tools, banned
    assert {n for n, t in tools.items() if t.tier == "read"} == {
        "browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_wait_for", "browser_tabs",
        "browser_close", "browser_hover"}
    assert {n for n, t in tools.items() if t.tier == "write"} == {
        "browser_click", "browser_type", "browser_fill_form", "browser_select_option", "browser_press_key",
        "browser_handle_dialog"}
    assert {t.toolgroup for t in tools.values()} == {"browser"}
    assert {n for n, t in tools.items() if t.observes == "page"} == set(br.PAGE_TOOLS)
    assert tools["browser_tabs"].observes is None and tools["browser_close"].observes is None


def test_a_missing_remote_tool_is_simply_absent():
    browser = br.Browser()
    tools = browser.adapt(FakeHub(), [t for t in remote_tools() if t.name != "browser_hover"])
    assert "browser_hover" not in {t.name for t in tools} and len(tools) == len(br.EXPOSED) - 1


# ---- reading a page -------------------------------------------------------------------------------------------
def test_parse_page_reads_the_url_and_the_named_elements_with_their_refs():
    url, elements = br.parse_page(PAGE)
    assert url == "http://127.0.0.1:8799/page.html"
    by_ref = {e.ref: (e.role, e.name, e.window) for e in elements}
    assert by_ref["e5"] == ("textbox", "Search", "127.0.0.1") and by_ref["e11"] == ("button", "Search", "127.0.0.1")
    assert by_ref["e13"] == ("link", "Go to other page", "127.0.0.1") and by_ref["e2"][0] == "heading"
    assert by_ref["f4e20"] == ("button", 'Pay "now"', "127.0.0.1")       # frame refs, and an escaped quote
    assert "e1" not in by_ref and "e3" not in by_ref                      # nameless containers
    assert "e15" not in by_ref                                             # a paragraph's text isn't a name
    assert all(e.role != "option" for e in elements)                       # no ref: nothing to act on
    assert br.parse_page("nothing here") == (None, [])
    assert br.parse_page("### Page\n- Page URL: https://www.bbc.co.uk/news\n")[0] == "https://www.bbc.co.uk/news"


async def test_an_action_result_is_the_page_not_playwrights_chatter(b):
    browser, tools, hub, tmp = b
    yml = tmp / "out" / "page-1.yml"
    yml.write_text(SNAPSHOT, encoding="utf-8")
    hub.replies["browser_navigate"] = (CODE + "### Page\n- Page URL: http://127.0.0.1:8799/page.html\n- Page Title: Probe form\n"
                                       f"### Snapshot\n- [Snapshot]({yml})\n### Events\n- New console entries: {tmp}\\console.log#L1")
    result = await run(tools["browser_navigate"], url="http://127.0.0.1:8799/page.html")
    assert result.ok and result.untrusted
    assert result.content.startswith("Page: Probe form — http://127.0.0.1:8799/page.html\n")
    assert 'button "Delete account" [ref=e12]' in result.content                 # the snapshot, read from its file
    assert "Ran Playwright code" not in result.content and "console" not in result.content   # chatter and local paths
    assert browser._elements["e12"].name == "Delete account" and browser._url == "http://127.0.0.1:8799/page.html"


async def test_a_snapshot_file_outside_the_output_folder_is_never_read(b):
    browser, tools, hub, tmp = b
    secret = tmp / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    hub.replies["browser_navigate"] = f"### Page\n- Page URL: https://x.example/\n- Page Title: X\n### Snapshot\n- [Snapshot]({secret})"
    result = await run(tools["browser_navigate"], url="https://x.example/")
    assert "TOP SECRET" not in result.content and result.content.startswith("Page: X — https://x.example/")


async def test_a_huge_snapshot_is_cut_with_a_note(b):
    browser, tools, hub, tmp = b
    big = "- generic [ref=e1]:\n" + "".join(f'  - link "item {i}" [ref=e{i + 2}]\n' for i in range(5000))
    hub.replies["browser_snapshot"] = "### Page\n- Page URL: https://x.example/\n- Page Title: X\n### Snapshot\n```yaml\n" + big + "```"
    result = await run(tools["browser_snapshot"])
    assert len(result.content) < br.MAX_SNAPSHOT_CHARS + 600 and "[snapshot cut" in result.content
    assert "item 0" in result.content and "item 4999" not in result.content


async def test_results_without_a_snapshot_are_short_and_errors_are_plain(b):
    _, tools, hub, _ = b
    hub.replies["browser_type"] = CODE
    done = await run(tools["browser_type"], target="e5", element="Search box", text="hi")
    assert done.ok and done.content == "Done."
    hub.replies["browser_tabs"] = "### Result\n- 0: (current) [Probe form](http://127.0.0.1:8799/page.html)"
    assert (await run(tools["browser_tabs"], action="list")).content == "- 0: (current) [Probe form](http://127.0.0.1:8799/page.html)"
    hub.replies["browser_click"] = "### Error\nError: Ref e99 not found in the current page snapshot. Try capturing new snapshot."
    failed = await run(tools["browser_click"], target="e99")
    assert not failed.ok and failed.content == "Ref e99 not found in the current page snapshot. Try capturing new snapshot."
    hub.replies["browser_wait_for"] = "### Result\nWaited for 1 seconds\n" + CODE + "### Page\n- Page URL: https://x.example/\n- Page Title: X\n### Snapshot\n```yaml\n- generic [ref=e1]\n```"
    waited = await run(tools["browser_wait_for"], time=1)
    assert waited.content.startswith("Waited for 1 seconds\nPage: X — https://x.example/")


# ---- risk -----------------------------------------------------------------------------------------------------------
async def ask(tool, **args):
    """A tool's verdict; an assessor may be async (it can ask the page something)."""
    out = tool.assess(args)
    return await out if hasattr(out, "__await__") else out


async def test_a_click_on_something_that_sends_buys_or_deletes_is_irreversible(b):
    _, tools, _, _ = b
    await see_the_page(tools)
    click = tools["browser_click"]
    delete = await ask(click, target="e12", element="button")
    assert (delete.verdict, delete.tier) == ("allow", "irreversible")      # allowed, but it asks: the tier says so
    assert "sends, buys or deletes" in delete.reason and "Delete account" in delete.target
    assert (await ask(click, target="f4e20")).tier == "irreversible"       # a frame ref, and the button "Pay now"
    search = await ask(click, target="e11")
    assert (search.verdict, search.tier) == ("allow", None) and "“Search”" in search.target and "127.0.0.1" in search.target
    # a ref we've never seen: the model's own description is judged instead
    assert (await ask(click, target="e77", element="Confirm purchase")).tier == "irreversible"
    assert (await ask(click, target="e77", element="Open the menu")).tier is None


async def test_navigation_only_goes_to_plain_web_pages_on_the_public_web(b):
    _, tools, _, _ = b
    nav = tools["browser_navigate"]
    assert (await ask(nav, url="https://example.com/a?b=1")).verdict == "allow"
    for bad in ("file:///C:/Windows/win.ini", "javascript:alert(1)", "chrome://settings", "about:blank", "ftp://x.example/",
                "http://127.0.0.1:8765/api/settings", "http://localhost:8080/", "http://10.0.0.5/admin",
                "http://[::1]/", "http://169.254.169.254/latest/meta-data", "not a url", ""):
        verdict = await ask(nav, url=bad)
        assert verdict.verdict == "deny", bad
    assert (await ask(tools["browser_tabs"], action="new", url="file:///C:/x")).verdict == "deny"
    assert (await ask(tools["browser_tabs"], action="list")).verdict == "allow"


async def test_it_never_types_passwords_or_codes(b):
    _, tools, hub, _ = b
    await see_the_page(tools)
    type_ = tools["browser_type"]
    assert (await ask(type_, target="e5", text="leeds library")).verdict == "allow"          # an ordinary search box
    for ref in ("e7", "e9"):                                                                  # named Password / "6-digit code"
        denied = await ask(type_, target=ref, text="hunter2")
        assert denied.verdict == "deny" and "I never type passwords or codes" in denied.reason
        assert "Settings → Browser" in denied.reason
    hub.credential["e14"] = "true"                                                            # "Your secret": a password input
    assert (await ask(type_, target="e14", text="x")).verdict == "deny"
    assert any(c[0] == "browser_evaluate" and c[1]["target"] == "e14" for c in hub.calls)    # asked the page, not guessed
    form = tools["browser_fill_form"]
    fields = [{"target": "e5", "name": "Search", "type": "textbox", "value": "a"},
              {"target": "e14", "name": "Your secret", "type": "textbox", "value": "b"}]
    assert (await ask(form, fields=fields)).verdict == "deny"
    assert (await ask(form, fields=fields[:1])).verdict == "allow"
    hub.credential["e5"] = None                                    # the page check can't run: judged by name alone
    assert (await ask(type_, target="e5", text="x")).verdict == "allow"
    assert (await ask(type_, target="e7", text="x")).verdict == "deny"


async def test_what_a_step_touched_is_recorded_from_the_page_before_the_call(b):
    browser, tools, hub, tmp = b
    await see_the_page(tools)
    other = "### Page\n- Page URL: https://other.example/x\n- Page Title: O\n### Snapshot\n```yaml\n- button \"Elsewhere\" [ref=e11]\n```"
    hub.replies["browser_click"] = other
    result = await run(tools["browser_click"], target="e11", element="Search button")
    assert result.meta == {"app": "browser", "element": {"role": "button", "name": "Search", "window": "127.0.0.1"}}
    again = await run(tools["browser_click"], target="e11")                       # the page is now the new one
    assert again.meta["element"] == {"role": "button", "name": "Elsewhere", "window": "other.example"}
    nav = await run(tools["browser_navigate"], url="https://z.example/")
    assert nav.meta == {"app": "browser"}


async def test_a_replay_picture_is_taken_after_a_successful_write_step_only_when_asked(b):
    browser, tools, hub, tmp = b
    await see_the_page(tools)
    assert (await run(tools["browser_click"], target="e11")).thumbnail is None            # setting off
    browser.thumbnails = lambda: True
    click = await run(tools["browser_click"], target="e11")
    from PIL import Image
    with Image.open(io.BytesIO(click.thumbnail)) as im:
        assert im.format == "JPEG" and im.width <= br.THUMB_WIDTH and im.height <= br.THUMB_WIDTH * 4
    assert ("browser_take_screenshot", {"scale": "css", "type": "jpeg"}) in hub.calls
    shots = sum(1 for c in hub.calls if c[0] == "browser_take_screenshot")
    assert (await run(tools["browser_snapshot"])).thumbnail is None and (await run(tools["browser_navigate"], url="https://a.example/")).thumbnail is None
    assert sum(1 for c in hub.calls if c[0] == "browser_take_screenshot") == shots       # looking takes no picture
    hub.replies["browser_click"] = "### Error\nboom"
    assert (await run(tools["browser_click"], target="e11")).thumbnail is None            # a failed step has no picture


def test_an_approval_for_a_site_covers_that_site_only(b):
    browser, tools, _, _ = b
    browser._url = "https://shop.example/cart"
    assert tools["browser_click"].scope_for({"target": "e1"}) == "shop.example"
    assert tools["browser_navigate"].scope_for({"url": "https://other.example/x"}) == "other.example"


# ---- the server's command line --------------------------------------------------------------------------------
def test_the_server_command_pins_the_package_and_closes_every_door_it_can(monkeypatch, tmp_path):
    monkeypatch.setattr(br, "aethel_home", lambda: tmp_path)
    monkeypatch.setattr(br.shutil, "which", lambda name: "C:\\node\\npx.cmd")
    stale = tmp_path / "browser" / "out"
    stale.mkdir(parents=True)
    (stale / "page-old.yml").write_text("old page text", encoding="utf-8")
    spec = br.browser_spec(br.Browser(), backend_port=8765, llama_ports=[8080, 8181], show=False)
    cmd = spec.command
    assert spec.name == "browser" and cmd[:3] == ["C:\\node\\npx.cmd", "-y", br.PACKAGE] and br.PACKAGE == "@playwright/mcp@0.0.83"

    def arg(flag):
        return cmd[cmd.index(flag) + 1]

    assert arg("--browser") == "msedge" and "--headless" in cmd and "--isolated" not in cmd
    assert arg("--user-data-dir") == str(tmp_path / "browser" / "profile")
    assert arg("--output-dir") == str(tmp_path / "browser" / "out") and arg("--file-paths") == "absolute"
    assert arg("--idle-timeout") == "600000" and "--no-webmcp" in cmd and "--caps" not in cmd
    assert "--allow-unrestricted-file-access" not in cmd
    blocked = arg("--blocked-origins").split(";")
    assert {"http://127.0.0.1:8765", "http://localhost:8765", "http://127.0.0.1:8080", "http://localhost:8080",
            "http://127.0.0.1:8181", "http://localhost:8181"} <= set(blocked)
    assert not list(stale.iterdir()) and (tmp_path / "browser" / "profile").is_dir()   # old page text isn't kept around


def test_show_browser_drops_headless_and_no_npx_means_no_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(br, "aethel_home", lambda: tmp_path)
    monkeypatch.setattr(br.shutil, "which", lambda name: "npx")
    assert "--headless" not in br.browser_spec(br.Browser(), 8765, [8080], show=True).command
    monkeypatch.setattr(br.shutil, "which", lambda name: None)
    assert br.browser_spec(br.Browser(), 8765, [8080], show=False) is None


# ---- wiring ---------------------------------------------------------------------------------------------------
def test_the_browser_is_one_of_the_default_servers_and_follows_the_show_setting(monkeypatch):
    from aethel.services import build_services
    from tests.fakes import FakeLocal, factory_from
    monkeypatch.setenv("AETHEL_MCP", "1")
    monkeypatch.setenv("AETHEL_PORT", "9123")
    svc = build_services(provider_factory=factory_from({}), local_llm=FakeLocal(up=False))
    spec = next(s for s in svc.mcp_servers if s.name == "browser")
    blocked = spec.command[spec.command.index("--blocked-origins") + 1].split(";")
    assert "http://127.0.0.1:9123" in blocked and "http://localhost:8080" in blocked    # the backend, and llama-server
    assert "--headless" in spec.command                                                  # hidden unless asked for
    svc.settings.update({"browser": {"show": True}})
    shown = next(s for s in build_services(provider_factory=factory_from({}), local_llm=FakeLocal(up=False)).mcp_servers
                 if s.name == "browser")
    assert "--headless" not in shown.command
    svc.close()


def test_the_servers_to_keep_the_browser_out_of_are_found_from_settings(monkeypatch):
    from aethel.services import local_ports
    from aethel.settings import AppSettings
    local = type("L", (), {"_port": lambda self: 8181})()
    plain = AppSettings.model_validate({})
    assert local_ports(local, plain) == [8181] and local_ports(None, plain) == [8080]
    mine = AppSettings.model_validate({"custom_base_url": "http://localhost:1234/v1"})
    assert local_ports(local, mine) == [1234, 8181]
    cloud = AppSettings.model_validate({"custom_base_url": "https://api.example.com/v1"})
    assert local_ports(local, cloud) == [8181]                                            # not a local server


async def test_what_the_snapshot_tool_returns_parses_back_into_the_same_elements(b):
    """Macro replay finds elements by parsing the snapshot tool's own result, so the two formats must agree."""
    _, tools, _, _ = b
    shown = (await run(tools["browser_snapshot"])).content
    assert shown.startswith("Page: Probe form — http://127.0.0.1:8799/page.html\n")
    url, again = br.parse_page(shown)
    assert url == "http://127.0.0.1:8799/page.html"
    assert [(e.ref, e.role, e.name, e.window) for e in again] == [(e.ref, e.role, e.name, e.window) for e in br.parse_page(PAGE)[1]]
    assert br.parse_page("Page: http://x.example/a")[0] == "http://x.example/a"          # a page with no title
