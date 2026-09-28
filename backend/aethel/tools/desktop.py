"""Any Windows app, through Windows-MCP (spec §4.2 universal layer).

Windows-MCP also ships PowerShell, FileSystem, Registry and Process tools that
would walk straight past the permission manifest, so only the UI tools below
are exposed. Every action is scoped to the app it lands in; Aethel's own
window and the system's credential prompts are never touched."""
import ctypes
import math
import os
import re
import shutil
from dataclasses import dataclass

import psutil
from mcp import types as mt

from ..safety.permissions import INTERPRETERS
from .base import Assessment, Tool, ToolContext, ToolResult
from .mcp_hub import ServerSpec

SERVER = "windows"
PACKAGE = "windows-mcp==0.8.6"
NEAR_PX = 30  # a click this close to an element's centre counts as clicking it

# remote name -> (local name, tier, assessor kind)
EXPOSED = {
    "App": ("win_app", "write", "app"),
    "Snapshot": ("win_snapshot", "read", None),
    "Click": ("win_click", "write", "pointer"),
    "Type": ("win_type", "write", "pointer"),
    "Scroll": ("win_scroll", "write", "pointer"),
    "Move": ("win_move", "write", "pointer"),
    "MultiSelect": ("win_multi_select", "write", "multi"),
    "MultiEdit": ("win_multi_edit", "write", "multi"),
    "Shortcut": ("win_shortcut", "write", "shortcut"),
    "Wait": ("win_wait", "read", None),
    "WaitFor": ("win_wait_for", "read", None),
    "Clipboard": ("win_clipboard", "write", "clipboard"),
    "DisplayInventory": ("win_displays", "read", None),
}
# Labels only mean something on an annotated screenshot the model never sees;
# the rest are screenshot options whose image we drop anyway.
HIDDEN_PARAMS = {"label", "labels", "use_vision", "width_reference_line", "height_reference_line"}

SHELLS = INTERPRETERS | {"command", "terminal", "wt", "regedit", "registry", "run"}
OFF_LIMITS = {"aethel", "consent", "credentialuibroker", "logonui"}  # Aethel, UAC and sign-in prompts
IRREVERSIBLE_RE = re.compile(
    r"\b(send|submit|pay|buy|purchase|order|checkout|check out|delete|remove|uninstall|install|sign out|"
    r"log out|discard|don'?t save|confirm|transfer|post|publish)\b", re.IGNORECASE)
IRREVERSIBLE_KEYS = {"alt+f4", "ctrl+w", "shift+delete", "ctrl+shift+delete"}
_ELEMENT_RE = re.compile(r'\((-?\d+),(-?\d+)\)\s+(\S+)\s+"(.*)"\s+\[action:')
_WINDOW_RE = re.compile(r'^window "(.*)"\s*$')


@dataclass
class Element:
    x: int
    y: int
    role: str
    name: str
    window: str


@dataclass
class App:
    pid: int
    name: str  # process name without .exe, e.g. "notepad"


def parse_snapshot(text: str) -> list[Element]:
    elements, window = [], ""
    for line in text.splitlines():
        header = _WINDOW_RE.match(line.strip())
        if header:
            window = header.group(1)
            continue
        m = _ELEMENT_RE.search(line)
        if m:
            elements.append(Element(int(m.group(1)), int(m.group(2)), m.group(3), m.group(4), window))
    return elements


def _app_of(hwnd) -> App | None:
    import win32gui
    import win32process
    if not hwnd:
        return None
    root = win32gui.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT: the top-level window
    _, pid = win32process.GetWindowThreadProcessId(root)
    try:
        name = psutil.Process(pid).name()
    except psutil.Error:
        return None
    return App(pid, name[:-4].lower() if name.lower().endswith(".exe") else name.lower())


def window_at(x: int, y: int) -> App | None:
    if os.name != "nt":
        return None
    import win32gui
    user32 = ctypes.windll.user32
    # Windows-MCP works in physical pixels; see the screen the same way.
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        return _app_of(win32gui.WindowFromPoint((x, y)))
    except Exception:
        return None
    finally:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


def foreground_window() -> App | None:
    if os.name != "nt":
        return None
    import win32gui
    try:
        return _app_of(win32gui.GetForegroundWindow())
    except Exception:
        return None


def _point(value) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        nums = value[:2]
    elif isinstance(value, str):
        nums = re.findall(r"-?\d+", value)[:2]
    else:
        return None
    try:
        return int(nums[0]), int(nums[1])
    except (IndexError, TypeError, ValueError):
        return None


def _app_name(name) -> str:
    name = str(name or "").strip().lower()
    return name[:-4] if name.endswith(".exe") else name


class Desktop:
    def __init__(self, window_at=window_at, foreground=foreground_window):
        self.window_at = window_at
        self.foreground = foreground
        self.hub = None
        self.elements: list[Element] = []  # from the latest snapshot

    # ---- where an action lands ------------------------------------------------
    def _near(self, pt: tuple[int, int] | None) -> Element | None:
        if pt is None or not self.elements:
            return None
        best = min(self.elements, key=lambda e: math.dist((e.x, e.y), pt))
        return best if math.dist((best.x, best.y), pt) <= NEAR_PX else None

    def _target_app(self, pt: tuple[int, int] | None) -> App | None:
        return self.window_at(*pt) if pt is not None else self.foreground()

    @staticmethod
    def _off_limits(app: App | None) -> bool:
        if app is None:
            return False
        return app.name.lower() in OFF_LIMITS or str(app.pid) == os.environ.get("AETHEL_PARENT_PID")

    def _points(self, kind: str, args: dict) -> list[tuple[int, int] | None]:
        if kind == "multi":
            return [_point(item) for item in (args.get("locs") or [])] or [None]
        return [_point(args.get("loc"))]

    def _scope(self, kind: str, args: dict) -> str:
        if kind == "app":
            return _app_name(args.get("name"))
        if kind == "clipboard":
            return "clipboard"
        app = self._target_app(self._points(kind, args)[0])
        return app.name if app else "unknown app"

    # ---- assessors ------------------------------------------------------------
    def _assess(self, kind: str, remote: str, args: dict) -> Assessment:
        if kind == "app":
            mode, name = args.get("mode") or "launch", str(args.get("name") or "")
            if mode == "launch_executable":
                return Assessment("ask", "This runs a program file directly.", f"Run {name}")
            if mode == "launch":
                words = set(re.split(r"[^a-z0-9]+", _app_name(name)))
                if words & SHELLS:
                    return Assessment("ask", "This opens a program that can run any command.", f"Open {name}")
                return Assessment("allow", "", f"Open {name}")
            return Assessment("allow", "", f"{mode.capitalize()} {name}".strip())
        if kind == "clipboard":
            reading = args.get("mode") == "get"
            return Assessment("allow", "", "Read the clipboard" if reading else "Put text on the clipboard")

        points = self._points(kind, args)
        apps = [self._target_app(pt) for pt in points]
        if any(self._off_limits(a) for a in apps):
            return Assessment("deny", "Aethel doesn't operate its own window or the system's sign-in prompts.",
                              f"{remote} in a protected window")
        where = apps[0].name if apps[0] else "the active window"
        if kind == "shortcut":
            keys = str(args.get("shortcut") or "").lower().replace(" ", "")
            target = f"Press {keys} in {where}"
            if "win" in keys.split("+"):
                return Assessment("ask", "Windows-key shortcuts open system features.", target)
            if keys in IRREVERSIBLE_KEYS:
                return Assessment("allow", "This can close a window or delete without asking.", target, "irreversible")
            return Assessment("allow", "", target)

        elements = [self._near(pt) for pt in points]
        named = next((e for e in elements if e is not None), None)
        if remote == "Type":
            text = str(args.get("text") or "")
            target = f"Type “{text[:60]}{'…' if len(text) > 60 else ''}” in {where}"
        else:
            what = f"“{named.name}”" if named else (f"at {points[0]}" if points[0] else "")
            target = f"{remote} {what} in {where}".replace("  ", " ")
        if remote in ("Click", "MultiSelect") and any(e and IRREVERSIBLE_RE.search(e.name) for e in elements):
            return Assessment("allow", "This looks like it sends, buys or deletes something.", target, "irreversible")
        return Assessment("allow", "", target)

    # ---- tools ----------------------------------------------------------------
    def _tool(self, remote: mt.Tool) -> Tool:
        name, tier, kind = EXPOSED[remote.name]
        schema = dict(remote.inputSchema or {"type": "object"})
        schema["properties"] = {k: v for k, v in (schema.get("properties") or {}).items() if k not in HIDDEN_PARAMS}
        if "required" in schema:
            schema["required"] = [r for r in schema["required"] if r not in HIDDEN_PARAMS]

        async def handler(args: dict, ctx: ToolContext | None) -> ToolResult:
            args = {k: v for k, v in args.items() if k not in HIDDEN_PARAMS}
            if remote.name == "Snapshot":
                args["use_vision"] = False
            result = await self.hub.call(SERVER, remote.name, args)
            if remote.name == "Snapshot" and result.ok:
                self.elements = parse_snapshot(result.content)
            return result

        if kind is None:
            assess = lambda args: Assessment("allow", "", remote.name)  # noqa: E731
            scope = None
        else:
            assess = lambda args: self._assess(kind, remote.name, args)  # noqa: E731
            scope = lambda args: self._scope(kind, args)  # noqa: E731
        return Tool(name, remote.description or name, schema, tier, handler, assess, grant_scope=scope,
                    group="desktop")

    def adapt(self, hub, remote_tools: list[mt.Tool]) -> list[Tool]:
        self.hub = hub
        return [self._tool(t) for t in remote_tools if t.name in EXPOSED]


def desktop_spec(desk: Desktop) -> ServerSpec | None:
    """None when uv isn't installed: computer control is then simply absent."""
    uvx = shutil.which("uvx")
    if uvx is None:
        return None
    return ServerSpec(SERVER, [uvx, PACKAGE, "serve"], desk.adapt, env={"ANONYMIZED_TELEMETRY": "false"})
