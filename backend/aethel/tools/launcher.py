"""Open web pages directly in a browser (spec §4.2, the universal launcher).

Handing the URL to the browser needs no typing into an address bar, so it
works whether or not the browser has focus, and a search is just its results
URL. Only http(s) URLs: a URL could otherwise smuggle browser flags or open
other protocols."""
import os
import re
import subprocess

from .base import Assessment, Tool, ToolContext, ToolResult

BROWSERS = {"firefox": "firefox.exe", "edge": "msedge.exe", "chrome": "chrome.exe", "brave": "brave.exe"}
_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def browser_path(name: str) -> str | None:
    """The installed browser's executable, from Windows' App Paths registry."""
    exe = BROWSERS.get(name)
    if exe is None or os.name != "nt":
        return None
    import winreg
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe)
            path = winreg.QueryValue(key, None)
            if path and os.path.isfile(path.strip('"')):
                return path.strip('"')
        except OSError:
            continue
    return None


def _launch(exe: str | None, url: str) -> None:
    if exe is None:
        os.startfile(url)  # the default browser
        return
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    subprocess.Popen([exe, url], close_fds=True, creationflags=flags)  # the browser outlives Aethel


def open_url_tool(find=browser_path, launch=_launch) -> Tool:
    def browser_of(args: dict) -> str:
        b = str(args.get("browser") or "default").lower()
        return b if b in BROWSERS else "default"

    def assess(args: dict) -> Assessment:
        url = str(args.get("url") or "").strip()
        if not _URL_RE.match(url):
            return Assessment("deny", "Only http:// or https:// web addresses can be opened.", url or "(no url)")
        return Assessment("allow", "", f"Open {url} in {browser_of(args)}")

    async def handler(args: dict, ctx: ToolContext) -> ToolResult:
        url, browser = str(args.get("url") or "").strip(), browser_of(args)
        exe = None
        if browser != "default":
            exe = find(browser)
            if exe is None:
                return ToolResult(False, f"{browser} doesn't seem to be installed. Try another browser or 'default'.")
        try:
            launch(exe, url)
        except OSError as exc:
            return ToolResult(False, f"Couldn't open the browser: {exc}")
        return ToolResult(True, f"Opened {url} in {browser}. It loads in its own time; take a win_snapshot "
                                "before clicking on the page.", meta={"app": browser})

    return Tool(
        "open_url",
        "Open a web page in a browser directly, without typing into it. For a search, open the results URL "
        "(e.g. https://www.youtube.com/results?search_query=lofi+music). Prefer this over typing into an "
        "address bar. browser: firefox, edge, chrome, brave or default.",
        {"type": "object", "properties": {
            "url": {"type": "string", "description": "An http:// or https:// address"},
            "browser": {"type": "string", "enum": ["default", *BROWSERS]}},
         "required": ["url"]},
        "write", handler, assess,
        grant_scope=browser_of, group="desktop",  # "Allow for this task" on Firefox covers opening pages in it
    )
