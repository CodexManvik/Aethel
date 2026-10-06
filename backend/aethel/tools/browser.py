"""Aethel's own background browser: Playwright MCP driving Edge, behind a vetted adapter (Phase 3 spec §7).

Probed 2026-10-07 against @playwright/mcp 0.0.83 (Microsoft, Apache-2.0; depends on playwright and playwright-core
1.64.0-alpha, and drives the installed Edge, so no browser is downloaded). What it does, as measured:
- It exposes 25 tools. Only the ones in EXPOSED reach the agent. Not exposed: browser_evaluate and
  browser_run_code_unsafe (arbitrary JS in a page), browser_file_upload (local files into a website), the console,
  network and storage tools, browser_find, drag/drop, resize and emulate. browser_take_screenshot is used by the
  adapter for replay pictures only (a text model can't read a picture). browser_evaluate is used by the adapter
  itself, with one fixed function, to tell a password field from an ordinary one.
- Element tools take `target` (a ref such as "e5" from the latest snapshot, frame refs look like "f4e12") and an
  optional human `element` description. They are NOT given a `ref` argument.
- browser_snapshot returns the page as YAML inline. Every action (navigate, click, hover, select, wait_for...) returns
  only "### Page" (URL, title) and a LINK to a snapshot file in --output-dir, plus a "### Ran Playwright code" echo and a
  console-log path. browser_type and browser_press_key return just the echo. The adapter reads the linked file (only
  from our own output folder), and gives the model the page and the YAML, not the chatter or local paths.
- The snapshot does not mark a password input: it is `textbox "Password"` like any other, and a field named
  "Your secret" is indistinguishable. A fixed browser_evaluate on the ref ("### Result\\ntrue") does tell them apart,
  and also catches autocomplete one-time-code and card-number fields.
- Native flags used: --idle-timeout (the browser closes after 10 idle minutes and relaunches on the next call),
  --blocked-origins (a navigation to a blocked origin fails with ERR_BLOCKED_BY_CLIENT; it does not cover redirects),
  --file-paths absolute, --output-max-size, --no-webmcp (a page can't register tools of its own), and file: URLs are
  blocked by the server unless --allow-unrestricted-file-access is given, which we never do.
- Network: the node process only talks to the npm registry (104.16.x.34, npx); it opens no other connection. Edge
  itself, headless and in a throwaway profile, still contacts Microsoft services (edge.microsoft.com and friends):
  that is Edge's own behaviour, not the package's.

Page content is untrusted: every result is wrapped by the engine and taints the task."""
import asyncio
import base64
import io
import logging
import re
import shutil
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

import anyio
from mcp import types as mt

from ..paths import aethel_home
from .base import Assessment, RiskTier, Tool, ToolContext, ToolResult
from .desktop import IRREVERSIBLE_RE, THUMB_WIDTH
from .mcp_hub import ServerSpec
from .web import BlockedAddress, _split, check_url, host_of

log = logging.getLogger("aethel.browser")

PACKAGE = "@playwright/mcp@0.0.83"
SERVER = "browser"
IDLE_TIMEOUT_MS = 600_000
OUTPUT_MAX_BYTES = 20_000_000
MAX_SNAPSHOT_CHARS = 24_000
MAX_SNAPSHOT_FILE_BYTES = 2_000_000

EXPOSED: dict[str, RiskTier] = {
    "browser_navigate": "read", "browser_navigate_back": "read", "browser_snapshot": "read",
    "browser_wait_for": "read", "browser_tabs": "read", "browser_close": "read", "browser_hover": "read",
    "browser_click": "write", "browser_type": "write", "browser_fill_form": "write",
    "browser_select_option": "write", "browser_press_key": "write", "browser_handle_dialog": "write",
}
# Tools whose result carries the state of the page: a newer one supersedes an older one in the model's context.
PAGE_TOOLS = {"browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_wait_for", "browser_click",
              "browser_type", "browser_fill_form", "browser_select_option", "browser_press_key", "browser_hover",
              "browser_handle_dialog"}
ELEMENT_TOOLS = {"browser_click", "browser_type", "browser_hover", "browser_select_option"}
CREDENTIAL_RE = re.compile(
    r"pass(word|code)|\bpin\b|one[- ]time|\botp\b|verification code|security code|\b\d+[- ]?digit\b|\b2fa\b|"
    r"two[- ]factor|authenticator|\bcvv\b|\bcvc\b|card number|recovery code", re.IGNORECASE)
REFUSED_CREDENTIALS = ("I never type passwords or codes. Sign in yourself (Settings → Browser → Sign in to sites…), "
                       "then ask me again.")
# One fixed function, run by the adapter on a field's ref before anything is typed into it. Never model-written.
CREDENTIAL_JS = ("(el) => { const t = (el.getAttribute('type') || el.type || '').toLowerCase(); "
                 "const a = (el.getAttribute('autocomplete') || '').toLowerCase(); "
                 "return t === 'password' || /(^|\\s)(one-time-code|current-password|new-password|cc-[a-z-]+)(\\s|$)/.test(a); }")

_ELEMENT_LINE = re.compile(r'^\s*- (?P<role>[\w-]+) "(?P<name>(?:[^"\\]|\\.)*)"(?P<attrs>(?: \[[^\]]*\])*)', re.MULTILINE)
_REF = re.compile(r"\[ref=(\w+)\]")
_SECTION = re.compile(r"^### (.+)$", re.MULTILINE)
_SNAPSHOT_LINK = re.compile(r"^- \[Snapshot\]\((.+)\)\s*$", re.MULTILINE)
_FENCE = re.compile(r"```(?:yaml)?\n(.*?)\n?```", re.DOTALL)
_UNESCAPE = re.compile(r"\\(.)")


def find_edge() -> str | None:
    """Microsoft Edge's executable, in the usual places."""
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(var)
        if base and (exe := Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe").is_file():
            return str(exe)
    return None


@dataclass
class PageElement:
    ref: str
    role: str
    name: str
    window: str  # the page's host, which is what stands in for a window when an element is found again


def parse_page(text: str) -> tuple[str | None, list[PageElement]]:
    """(the page's URL, its named elements with refs) from a snapshot result. Tolerant: whatever doesn't match is skipped."""
    found = re.search(r"^- Page URL: (\S+)", text, re.MULTILINE)
    url = found.group(1) if found else None
    host = host_of(url) if url else None
    elements = []
    for m in _ELEMENT_LINE.finditer(text):
        ref = _REF.search(m.group("attrs"))
        if ref is not None:
            elements.append(PageElement(ref.group(1), m.group("role"), _UNESCAPE.sub(r"\1", m.group("name")), host or ""))
    return url, elements


def _sections(text: str) -> dict[str, str]:
    marks = list(_SECTION.finditer(text))
    return {m.group(1).strip(): text[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(text))].strip("\n")
            for i, m in enumerate(marks)}


def _shrink(image_b64: str) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(base64.b64decode(image_b64))) as im:
        im.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 4))
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=70)
        return out.getvalue()


class Browser:
    def __init__(self, thumbnails=lambda: False, out_dir: Path | None = None, profile_dir: Path | None = None,
                 popen=subprocess.Popen, find_edge=find_edge):
        self.thumbnails = thumbnails  # whether to keep a replay picture after each step that changes a page
        self.out_dir = out_dir if out_dir is not None else aethel_home() / "browser" / "out"
        self.profile_dir = profile_dir if profile_dir is not None else aethel_home() / "browser" / "profile"
        self.popen, self.find_edge = popen, find_edge
        self._sign_in = None  # the Edge window the user was given to sign in with, while it's open
        self.hub = None
        self._url: str | None = None                  # the page the model last saw
        self._elements: dict[str, PageElement] = {}   # ...and its elements by ref

    # ---- signing in: the user's own window on the same profile --------------------------------------
    def signing_in(self) -> bool:
        return self._sign_in is not None and self._sign_in.poll() is None

    def open_sign_in(self) -> bool:
        """Open Edge, visible, on Aethel's profile so the user logs in themselves. False if Edge can't be found."""
        exe = self.find_edge()
        if exe is None:
            return False
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        self._sign_in = self.popen([exe, f"--user-data-dir={self.profile_dir}", "--no-first-run", "about:blank"],
                                   close_fds=True, creationflags=flags)
        return True

    # ---- what the page looked like ----------------------------------------------------------------
    def _snapshot_file(self, link: str) -> str | None:
        """The text of a snapshot file the server linked, if it really is a file in our own output folder."""
        try:
            path = Path(link.strip()).resolve()
            if not path.is_relative_to(self.out_dir.resolve()) or path.stat().st_size > MAX_SNAPSHOT_FILE_BYTES:
                return None
            return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return None

    def _digest(self, text: str) -> tuple[str, str | None, str | None]:
        """(what the model sees, the page's URL, its snapshot YAML) for one result: the page and what it holds, not
        Playwright's echo of the code it ran or its console-log paths."""
        sections = _sections(text)
        snap_body = sections.get("Snapshot", "")
        fence = _FENCE.search(snap_body)
        link = _SNAPSHOT_LINK.search(snap_body)
        yaml = fence.group(1) if fence else (self._snapshot_file(link.group(1)) if link else None)
        page = sections.get("Page", "")
        url = (re.search(r"^- Page URL: (\S+)", page, re.MULTILINE) or [None, None])[1]
        title = (re.search(r"^- Page Title: (.*)$", page, re.MULTILINE) or [None, None])[1]
        parts = []
        if sections.get("Result"):
            parts.append(sections["Result"].strip())
        if url:
            parts.append(f"Page: {title} — {url}" if title else f"Page: {url}")
        if yaml:
            lines = yaml.rstrip().split("\n")
            kept, size = [], 0
            for i, line in enumerate(lines):
                size += len(line) + 1
                if size > MAX_SNAPSHOT_CHARS:
                    kept.append(f"[snapshot cut: {len(lines) - i} more lines. Use browser_snapshot, or act on what is "
                                "above and look again.]")
                    break
                kept.append(line)
            parts.append("\n".join(kept))
        return "\n".join(parts) or "Done.", url, yaml

    def _remember(self, url: str | None, yaml: str | None) -> None:
        """The page as the model last saw it. Refs belong to one snapshot, so each new one replaces the last."""
        if yaml is None:
            return
        self._url, elements = parse_page(f"- Page URL: {url}\n{yaml}" if url else yaml)
        self._url = url or self._url
        self._elements = {e.ref: e for e in elements}

    # ---- risk -------------------------------------------------------------------------------------
    def _named(self, args: dict) -> tuple[PageElement | None, list[str]]:
        """The element a call targets (from the page the model saw) and every name that describes it."""
        el = self._elements.get(str(args.get("target") or ""))
        names = [el.name] if el else []
        if args.get("element"):
            names.append(str(args["element"]))
        return el, names

    async def _is_credential_field(self, ref: str) -> bool | None:
        """True/False from the page itself (a fixed browser_evaluate on the ref); None when it couldn't be asked."""
        result = await self.hub.call_raw(SERVER, "browser_evaluate", {"target": ref, "function": CREDENTIAL_JS})
        if isinstance(result, str) or result.isError:
            return None
        text = "\n".join(c.text for c in result.content if isinstance(c, mt.TextContent))
        answer = re.search(r"### Result\s*\n\s*(true|false)\b", text)
        return None if answer is None else answer.group(1) == "true"

    async def _typing_into_credentials(self, args: dict) -> bool:
        """Whether this call would type into a password, code or card field: by what the field is called, then by
        what the page says it is."""
        if args.get("fields") is not None:  # browser_fill_form
            fields = [f for f in args["fields"] if isinstance(f, dict)]
            probes = [(f, [str(f.get("name") or ""), *self._named({"target": f.get("target"), "element": f.get("element")})[1]])
                      for f in fields]
        else:
            probes = [(args, self._named(args)[1])]
        for field, names in probes:
            if any(CREDENTIAL_RE.search(n) for n in names):
                return True
            ref = str(field.get("target") or "")
            if ref and await self._is_credential_field(ref):
                return True
        return False

    async def _assess_navigation(self, url: str, what: str) -> Assessment:
        parts = _split(url)
        if parts is None or parts.scheme not in ("http", "https"):
            return Assessment("deny", "Only http:// or https:// pages can be opened in the browser.", url or "(no address)")
        try:
            await anyio.to_thread.run_sync(check_url, url, abandon_on_cancel=True)
        except BlockedAddress:
            return Assessment("deny", "That address is this computer or a private network, not the public web.", url)
        except socket.gaierror:
            pass  # a name that doesn't exist: the browser will say so
        return Assessment("allow", "", f"{what} {url}")

    def _assess(self, remote: str):
        async def assess(args: dict) -> Assessment:
            if self.signing_in():  # the user's window holds the profile: starting Edge on it would fail or fight them
                return Assessment("deny", "You have a browser window open for signing in. Close it, then ask me "
                                          "again.", "The browser is open for you to sign in")
            if remote == "browser_navigate":
                return await self._assess_navigation(str(args.get("url") or "").strip(), "Go to")
            if remote == "browser_tabs":
                if args.get("action") == "new" and args.get("url"):
                    return await self._assess_navigation(str(args["url"]).strip(), "Open a tab on")
                return Assessment("allow", "", f"{str(args.get('action') or 'list').capitalize()} tabs")
            where = host_of(self._url or "") or "the page"
            el, names = self._named(args)
            what = f"“{el.name}”" if el else (f"“{args['element']}”" if args.get("element") else "")
            if remote in ("browser_type", "browser_fill_form") and await self._typing_into_credentials(args):
                return Assessment("deny", REFUSED_CREDENTIALS, f"Type into {what or 'a field'} on {where}")
            if remote == "browser_type":
                text = str(args.get("text") or "")
                target = f"Type “{text[:60]}{'…' if len(text) > 60 else ''}” into {what or 'a field'} on {where}"
            else:
                verb = {"browser_click": "Click", "browser_hover": "Hover over", "browser_select_option": "Choose in",
                        "browser_fill_form": "Fill in a form on", "browser_press_key": f"Press {args.get('key', 'a key')} on",
                        "browser_handle_dialog": "Answer a dialog on"}.get(remote, remote)
                target = f"{verb} {what} on {where}".replace("  ", " ") if what else f"{verb} {where}"
            if remote in ("browser_click", "browser_select_option", "browser_press_key") \
                    and any(IRREVERSIBLE_RE.search(n) for n in names):
                return Assessment("allow", "This looks like it sends, buys or deletes something.", target, "irreversible")
            return Assessment("allow", "", target)
        return assess

    def _scope(self, remote: str):
        def scope(args: dict) -> str:  # "Allow for this task" covers one site
            url = args.get("url") if remote in ("browser_navigate", "browser_tabs") else None
            return host_of(str(url or "")) or host_of(self._url or "") or "browser"
        return scope

    # ---- the adapter ------------------------------------------------------------------------------
    async def _picture(self) -> bytes | None:
        shot = await self.hub.call_raw(SERVER, "browser_take_screenshot", {"scale": "css", "type": "jpeg"})
        image = None if isinstance(shot, str) else next((c for c in shot.content if isinstance(c, mt.ImageContent)), None)
        if image is None:
            return None
        try:
            return await anyio.to_thread.run_sync(_shrink, image.data)
        except Exception:
            log.warning("couldn't make a replay picture of the page")
            return None

    def _tool(self, remote: mt.Tool) -> Tool:
        name, tier = remote.name, EXPOSED[remote.name]

        async def handler(args: dict, ctx: ToolContext | None) -> ToolResult:
            el, _ = self._named(args) if name in ELEMENT_TOOLS else (None, [])  # the page as it was before the call
            raw = await self.hub.call_raw(SERVER, name, args)
            if isinstance(raw, str):
                return ToolResult(False, raw)
            text = "\n".join(c.text for c in raw.content if isinstance(c, mt.TextContent)).strip()
            if raw.isError:
                err = _sections(text).get("Error", text).split("Call log:")[0].strip()  # the log is noise to the model
                err = re.sub(r"^Error:\s*", "", err)
                return ToolResult(False, (err or "The browser reported an error.")[:1500], untrusted=True)
            content, url, yaml = self._digest(text)
            self._remember(url, yaml)
            meta = {"app": "browser"}
            if el is not None:
                meta["element"] = {"role": el.role, "name": el.name, "window": el.window}
            result = ToolResult(True, content, untrusted=True, meta=meta)
            if tier == "write" and self.thumbnails():
                result.thumbnail = await self._picture()
            return result

        return Tool(name, remote.description or name, remote.inputSchema or {"type": "object"}, tier, handler,
                    self._assess(name), grant_scope=self._scope(name), group="browser",
                    observes="page" if name in PAGE_TOOLS else None, toolgroup="browser")

    def adapt(self, hub, remote_tools: list[mt.Tool]) -> list[Tool]:
        self.hub = hub
        return [self._tool(t) for t in remote_tools if t.name in EXPOSED]


def browser_spec(browser: Browser, backend_port: int, llama_ports: list[int], show: bool) -> ServerSpec | None:
    """None when npx isn't installed: the background browser is then simply absent."""
    npx = shutil.which("npx")
    if npx is None:
        return None
    home = aethel_home() / "browser"
    profile, out = home / "profile", home / "out"
    profile.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    browser.profile_dir = profile
    for stale in out.iterdir():  # snapshots and screenshots of earlier pages aren't kept around between runs
        shutil.rmtree(stale, ignore_errors=True) if stale.is_dir() else stale.unlink(missing_ok=True)
    browser.out_dir = out
    blocked = ";".join(f"http://{host}:{port}" for port in [backend_port, *llama_ports] for host in ("127.0.0.1", "localhost"))
    cmd = [npx, "-y", PACKAGE, "--browser", "msedge", "--user-data-dir", str(profile), "--output-dir", str(out),
           "--output-max-size", str(OUTPUT_MAX_BYTES), "--file-paths", "absolute", "--idle-timeout", str(IDLE_TIMEOUT_MS),
           "--no-webmcp", "--blocked-origins", blocked]
    if not show:
        cmd.append("--headless")
    return ServerSpec(SERVER, cmd, browser.adapt)  # no telemetry setting exists to turn off: see the probe notes above
