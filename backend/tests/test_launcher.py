import pytest

from aethel.tools.base import ToolContext
from aethel.tools.launcher import open_url_tool

pytestmark = pytest.mark.anyio


def make():
    opened = []
    tool = open_url_tool(find=lambda b: {"firefox": "C:/ff/firefox.exe"}.get(b),
                         launch=lambda exe, url: opened.append((exe, url)))
    return tool, opened


def test_only_web_addresses_and_grants_follow_the_browser():
    tool, _ = make()
    assert tool.assess({"url": "https://www.youtube.com/results?search_query=lofi+music"}).verdict == "allow"
    for bad in ["file:///C:/Windows/win.ini", "-profile C:/x", "ms-settings:privacy", "javascript:alert(1)", ""]:
        assert tool.assess({"url": bad}).verdict == "deny", bad
    assert tool.scope_for({"url": "https://x.org", "browser": "Firefox"}) == "firefox"
    assert tool.group == "desktop" and tool.tier == "write"


async def test_opens_in_the_named_or_default_browser():
    tool, opened = make()
    ok = await tool.handler({"url": "https://www.youtube.com", "browser": "firefox"}, ToolContext(None))
    assert ok.ok and opened[-1] == ("C:/ff/firefox.exe", "https://www.youtube.com")
    await tool.handler({"url": "https://example.org"}, ToolContext(None))
    assert opened[-1] == (None, "https://example.org")
    missing = await tool.handler({"url": "https://example.org", "browser": "chrome"}, ToolContext(None))
    assert not missing.ok and "doesn't seem to be installed" in missing.content
