"""Any Windows app, through Windows-MCP (spec §4.2 universal layer).

Windows-MCP also ships PowerShell, FileSystem, Registry and Process tools that
would walk straight past the permission manifest, so only the UI tools below
are exposed. Every action is scoped to the app it lands in; Aethel's own
window and the system's credential prompts are never touched."""
import asyncio
import base64
import ctypes
import io
import math
import os
import re
import shutil
from contextlib import aclosing
from dataclasses import dataclass

import anyio
import psutil
from mcp import types as mt

from ..providers.base import ChatMessage, TextDelta
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
_SCALE_RE = re.compile(r"Screenshot Coordinate Scale:\s*([\d.]+)")
SCREEN_TOOLS = {"App", "Click", "Type", "Scroll", "Move", "Shortcut", "MultiSelect", "MultiEdit"}  # change the screen
THUMB_WIDTH = 480
CURSOR_LEAD_S = 0.2


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


WS_EX_TRANSPARENT = 0x20
GWL_EXSTYLE = -20
GW_HWNDNEXT = 2


def _click_through(hwnd) -> bool:
    import win32gui
    return bool(win32gui.GetWindowLong(hwnd, GWL_EXSTYLE) & WS_EX_TRANSPARENT)


def _window_under(x: int, y: int):
    """The top-level window a click at (x, y) reaches: click-through windows,
    such as Aethel's own ghost cursor overlay, are looked through."""
    import win32gui
    hwnd = win32gui.WindowFromPoint((x, y))
    root = win32gui.GetAncestor(hwnd, 2) if hwnd else 0
    while root and _click_through(root):
        root = win32gui.GetWindow(root, GW_HWNDNEXT)
        while root and not (win32gui.IsWindowVisible(root) and not _click_through(root)
                            and _contains(win32gui.GetWindowRect(root), x, y)):
            root = win32gui.GetWindow(root, GW_HWNDNEXT)
    return root


def _contains(rect, x: int, y: int) -> bool:
    left, top, right, bottom = rect
    return left <= x < right and top <= y < bottom


def window_at(x: int, y: int) -> App | None:
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    # Windows-MCP works in physical pixels; see the screen the same way.
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        return _app_of(_window_under(x, y))
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
    def __init__(self, window_at=window_at, foreground=foreground_window, vision=None, thumbnails=lambda: False,
                 on_pointer=None):
        self.on_pointer = on_pointer  # async (task_id, x, y, label): the ghost cursor, before each pointer action
        self.window_at = window_at
        self.foreground = foreground
        self.thumbnails = thumbnails  # whether to keep a replay thumbnail after each on-screen step
        self.vision = vision  # a RoleRouter: win_locate asks its "vision" role
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

    # ---- replay: what a step touched, and the screen after it ------------------
    def _meta(self, kind: str, args: dict) -> dict:
        if kind == "app":
            return {"app": _app_name(args.get("name"))}
        pt = self._points(kind, args)[0]
        app = self._target_app(pt)
        el = self._near(pt)
        return {"app": app.name if app else None,
                "element": {"role": el.role, "name": el.name, "window": el.window} if el else None}

    async def _thumbnail(self) -> bytes | None:
        shot = await self.hub.call_raw(SERVER, "Screenshot", {"use_annotation": False})
        image = None if isinstance(shot, str) else next(
            (c for c in shot.content if isinstance(c, mt.ImageContent)), None)
        if image is None:
            return None

        def shrink() -> bytes:
            from PIL import Image
            with Image.open(io.BytesIO(base64.b64decode(image.data))) as im:
                im.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 4))
                out = io.BytesIO()
                im.convert("RGB").save(out, "JPEG", quality=70)
                return out.getvalue()

        try:
            return await anyio.to_thread.run_sync(shrink)
        except Exception:
            return None

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
            touches = remote.name in SCREEN_TOOLS
            meta = self._meta(kind, args) if touches and kind is not None else None  # before the screen changes
            if self.on_pointer is not None and kind in ("pointer", "multi"):
                pt = self._points(kind, args)[0]
                if pt is not None:
                    await self.on_pointer(ctx.task_id if ctx else None, pt[0], pt[1],
                                          self._assess(kind, remote.name, args).target)
                    await asyncio.sleep(CURSOR_LEAD_S)  # let the cursor arrive before the click
            result = await self.hub.call(SERVER, remote.name, args)
            if remote.name == "Snapshot" and result.ok:
                self.elements = parse_snapshot(result.content)
            if touches and result.ok:
                result.meta = meta
                if self.thumbnails():
                    result.thumbnail = await self._thumbnail()
            return result

        if kind is None:
            assess = lambda args: Assessment("allow", "", remote.name)  # noqa: E731
            scope = None
        else:
            assess = lambda args: self._assess(kind, remote.name, args)  # noqa: E731
            scope = lambda args: self._scope(kind, args)  # noqa: E731
        return Tool(name, remote.description or name, schema, tier, handler, assess, grant_scope=scope,
                    group="desktop")

    async def _locate(self, args: dict, ctx: ToolContext | None) -> ToolResult:
        """Vision fallback (spec §4.2): for apps whose accessibility tree is
        empty or unhelpful, find a described thing on a screenshot."""
        description = str(args.get("description") or "").strip()
        if not description:
            return ToolResult(False, "Say what to look for.")
        shot = await self.hub.call_raw(SERVER, "Screenshot", {"use_annotation": False})
        if isinstance(shot, str):
            return ToolResult(False, shot)
        image = next((c for c in shot.content if isinstance(c, mt.ImageContent)), None)
        if image is None:
            return ToolResult(False, "Couldn't take a screenshot.")
        notes = " ".join(c.text for c in shot.content if isinstance(c, mt.TextContent))
        m = _SCALE_RE.search(notes)
        scale = float(m.group(1)) if m else 1.0
        prompt = (f"Find this on the screenshot: {description}\n"
                  "Reply with only the centre of it as x,y in image pixels, or none if it isn't there.")
        reply = []
        stream = self.vision.stream("vision", [ChatMessage("user", prompt,
                                                           images=[f"data:{image.mimeType};base64,{image.data}"])],
                                    max_tokens=40)
        async with aclosing(stream):
            async for event in stream:
                if isinstance(event, TextDelta):
                    reply.append(event.text)
        point = re.search(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", "".join(reply))
        if point is None:
            return ToolResult(False, f"I couldn't see {description} on the screen.")
        # ponytail: assumes the screenshot starts at the desktop's top-left; a
        # monitor left of or above the primary one would need its offset added.
        x, y = round(float(point.group(1)) * scale), round(float(point.group(2)) * scale)
        return ToolResult(True, f"{description} is at ({x}, {y}). Pass [{x}, {y}] as loc.", untrusted=True)

    def adapt(self, hub, remote_tools: list[mt.Tool]) -> list[Tool]:
        self.hub = hub
        tools = [self._tool(t) for t in remote_tools if t.name in EXPOSED]
        if self.vision is not None and any(t.name == "Screenshot" for t in remote_tools):
            tools.append(Tool(
                "win_locate",
                "Find something on screen by description using a screenshot, when win_snapshot doesn't list it "
                "(games, canvases, some Electron apps). Returns screen coordinates to pass as loc.",
                {"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]},
                "read", self._locate, lambda args: Assessment("allow", "", f"Look for {args.get('description')}"),
                group="desktop"))
        return tools


def desktop_spec(desk: Desktop) -> ServerSpec | None:
    """None when uv isn't installed: computer control is then simply absent."""
    uvx = shutil.which("uvx")
    if uvx is None:
        return None
    return ServerSpec(SERVER, [uvx, PACKAGE, "serve"], desk.adapt, env={"ANONYMIZED_TELEMETRY": "false"})
