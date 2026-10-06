"""Aethel's own background browser: Playwright MCP driving Edge, behind a vetted adapter (Phase 3 spec §7).

Probed 2026-10-07 against @playwright/mcp 0.0.83 (Microsoft, Apache-2.0; depends on playwright and playwright-core
1.64.0-alpha, and drives the installed Edge, so no browser is downloaded). What it does, as measured:
- It exposes 25 tools. Only the ones in EXPOSED reach the agent, and only with the arguments in ALLOWED_ARGS (the
  server's `filename` argument writes a page into any file under the backend's folder, so no argument the adapter
  hasn't vetted is passed on or shown to the model). Not exposed: browser_evaluate and browser_run_code_unsafe
  (arbitrary JS in a page), browser_file_upload (local files into a website), the console, network and storage
  tools, browser_find, drag/drop, resize and emulate. browser_take_screenshot is used by the adapter for replay
  pictures only (a text model can't read a picture). browser_evaluate is used by the adapter itself, with two fixed
  functions, to tell a password field from an ordinary one.
- Element tools take `target` (a ref such as "e5" from the latest snapshot, frame refs look like "f4e12") and an
  optional human `element` description. They are NOT given a `ref` argument. A target that is not a ref is a
  Playwright selector, which has no name the adapter could judge, so the adapter refuses it.
- browser_snapshot returns the page as YAML inline. Every action (navigate, click, hover, select, wait_for...) returns
  only "### Page" (URL, title) and a LINK to a snapshot file in --output-dir, plus a "### Ran Playwright code" echo and a
  console-log path. browser_type and browser_press_key return just the echo. The adapter reads the linked file (only
  from our own output folder), and gives the model the page and the YAML, not the chatter or local paths.
- A result is text with "### " headers in the order Error/Result, code echo, Open tabs, Page, Modal state, Snapshot,
  Events. A dialog's message is page-controlled and may hold newlines, so it can contain forged headers: the real
  Page comes before it and the real Snapshot after it, which is how _parse_result tells them apart.
- The snapshot does not mark a password input: it is `textbox "Password"` like any other, and a field named
  "Your secret" is indistinguishable. A fixed browser_evaluate on the ref ("### Result\\ntrue") does tell them apart,
  and also catches autocomplete one-time-code and card-number fields; it works on a page whose Content-Security-
  Policy blocks the page's own scripts. It runs in the page's own world, so a hostile page can lie to it: a limit.
- Native flags used: --idle-timeout (the browser closes after 10 idle minutes and relaunches on the next call),
  --blocked-origins (a navigation to a blocked origin fails with ERR_BLOCKED_BY_CLIENT; the wildcard-port form
  http://127.0.0.1:* blocks every local port; it does not cover redirects, which is why the page the browser ended
  up on is checked after every action), --file-paths absolute, --output-max-size, --no-webmcp (a page can't register
  tools of its own), and file: URLs are blocked by the server unless --allow-unrestricted-file-access is given,
  which we never do. npx --prefer-offline keeps it from asking the npm registry on every launch.
- Network: the node process only talks to the npm registry (104.16.x.34, npx); it opens no other connection. Edge
  itself, headless and in a throwaway profile, still contacts Microsoft services (edge.microsoft.com and friends):
  that is Edge's own behaviour, not the package's.

Page content is untrusted: every result is wrapped by the engine and taints the task."""
import base64
import io
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import anyio
from mcp import types as mt

from ..paths import aethel_home
from .base import Assessment, RiskTier, Tool, ToolContext, ToolResult
from .desktop import IRREVERSIBLE_RE, THUMB_WIDTH
from .mcp_hub import ServerSpec
from .web import _WEB_ADDRESS, BlockedAddress, SourceList, _split, check_url, host_of

log = logging.getLogger("aethel.browser")

PACKAGE = "@playwright/mcp@0.0.83"
SERVER = "browser"
IDLE_TIMEOUT_MS = 600_000
OUTPUT_MAX_BYTES = 20_000_000
MAX_SNAPSHOT_CHARS = 24_000
MAX_SNAPSHOT_FILE_BYTES = 2_000_000
SIGN_IN_MAX_SECONDS = 30 * 60  # Edge can stay running after its window closes: don't refuse the browser forever

EXPOSED: dict[str, RiskTier] = {
    "browser_navigate": "read", "browser_navigate_back": "read", "browser_snapshot": "read",
    "browser_wait_for": "read", "browser_tabs": "read", "browser_close": "read", "browser_hover": "read",
    "browser_click": "write", "browser_type": "write", "browser_fill_form": "write",
    "browser_select_option": "write", "browser_press_key": "write", "browser_handle_dialog": "write",
}
# The only arguments passed to each tool, and shown to the model. Anything else the server accepts (above all
# `filename`, which writes a file wherever the model says) is dropped.
ALLOWED_ARGS: dict[str, set[str]] = {
    "browser_navigate": {"url"}, "browser_navigate_back": set(), "browser_close": set(),
    "browser_snapshot": {"target", "depth"}, "browser_wait_for": {"time", "text", "textGone"},
    "browser_tabs": {"action", "index", "url"}, "browser_hover": {"target", "element"},
    "browser_click": {"target", "element", "doubleClick", "button", "modifiers"},
    "browser_type": {"target", "element", "text", "submit", "slowly"}, "browser_fill_form": {"fields"},
    "browser_select_option": {"target", "element", "values"}, "browser_press_key": {"key"},
    "browser_handle_dialog": {"accept", "promptText"},
}
# Tools whose result usually carries the state of the page: a newer one supersedes an older one in the model's context.
PAGE_TOOLS = {"browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_wait_for", "browser_click",
              "browser_type", "browser_fill_form", "browser_select_option", "browser_press_key", "browser_hover",
              "browser_handle_dialog"}
ELEMENT_TOOLS = {"browser_click", "browser_type", "browser_hover", "browser_select_option"}
NAVIGATION_KEYS = {"Tab", "Shift+Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "PageUp",
                   "PageDown", "Home", "End"}  # moving about types nothing
CREDENTIAL_RE = re.compile(
    r"pass(word|code)|\bpin\b|one[- ]time|\botp\b|verification code|security code|\b\d+[- ]?digit\b|\b2fa\b|"
    r"two[- ]factor|authenticator|\bcvv\b|\bcvc\b|card number|recovery code", re.IGNORECASE)
REFUSED_CREDENTIALS = ("I never type passwords or codes. Sign in yourself (Settings → Browser → Sign in to sites…), "
                       "then ask me again.")
UNSURE_CREDENTIALS = "I couldn't check whether this is a password or code field, so I'm asking before typing into it."
NOT_A_REF = ("Act on an element by its ref from the latest snapshot (like e5), not a selector: a selector has no name "
             "for me to check. Take a browser_snapshot and use the ref it shows.")
# Two fixed functions, run by the adapter before anything is typed. Never model-written.
_CREDENTIAL_TEST = ("const t = (el.getAttribute('type') || el.type || '').toLowerCase(); "
                    "const a = (el.getAttribute('autocomplete') || '').toLowerCase(); "
                    "return t === 'password' || /(^|\\s)(one-time-code|current-password|new-password|cc-[a-z-]+)(\\s|$)/.test(a);")
CREDENTIAL_JS = "(el) => { " + _CREDENTIAL_TEST + " }"
FOCUSED_CREDENTIAL_JS = "() => { const el = document.activeElement; if (!el) return false; " + _CREDENTIAL_TEST + " }"

_REF = re.compile(r"^(f\d+)?e\d+$")
_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]*|\d+)$", re.IGNORECASE)   # a last label like this is an IPv4 in disguise
_DNS_LABEL = re.compile(r"^[a-z0-9-]{1,63}$", re.IGNORECASE)
_ELEMENT_LINE = re.compile(r'^\s*- (?P<role>[\w-]+) "(?P<name>(?:[^"\\]|\\.)*)"(?P<attrs>(?: \[[^\]]*\])*)', re.MULTILINE)
_CLICKABLE_TEXT = re.compile(r"^\s*- (?P<role>[\w-]+)(?P<attrs>(?: \[[^\]]*\])+): (?P<text>\S.*)$", re.MULTILINE)
_LINK_LINE = re.compile(r"^\s*- /url: (\S+)\s*$", re.MULTILINE)
_REF_ATTR = re.compile(r"\[ref=(\w+)\]")
_HEADER = re.compile(r"^### (.+)$", re.MULTILINE)
_SNAPSHOT_LINK = re.compile(r"^- \[Snapshot\]\((.+)\)\s*$", re.MULTILINE)
_FENCE = re.compile(r"```(?:yaml)?\n(.*?)\n?```", re.DOTALL)
_MODAL = re.compile(r"^- \[(.*)\]: can be handled by \w+", re.DOTALL | re.MULTILINE)
_PAGE_URL = re.compile(r"^- Page URL: (\S+)|^Page: (?:.* — )?(\S+)\s*$", re.MULTILINE)
_PAGE_TITLE = re.compile(r"^- Page Title: (.*)$", re.MULTILINE)


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


def _decode(name: str) -> str:
    """A quoted snapshot name as the server wrote it (JSON-style escapes)."""
    try:
        return json.loads(f'"{name}"')
    except ValueError:
        return name


def parse_page(text: str) -> tuple[str | None, list[PageElement]]:
    """(the page's URL, its named elements with refs) from a snapshot result. Tolerant: whatever doesn't match is
    skipped. A name is the quoted one, or for a clickable element with no quoted name (a div that acts as a button)
    the text on its line; plain text is not an element."""
    found = _PAGE_URL.search(text)
    url = (found.group(1) or found.group(2)) if found else None
    host = host_of(url) if url else None
    elements = []
    for m in _ELEMENT_LINE.finditer(text):
        ref = _REF_ATTR.search(m.group("attrs"))
        if ref is not None:
            elements.append(PageElement(ref.group(1), m.group("role"), _decode(m.group("name")), host or ""))
    for m in _CLICKABLE_TEXT.finditer(text):
        ref = _REF_ATTR.search(m.group("attrs"))
        if ref is not None and "cursor=pointer" in m.group("attrs"):
            elements.append(PageElement(ref.group(1), m.group("role"), " ".join(m.group("text").split())[:120], host or ""))
    return url, elements


def page_links(text: str, base: str | None) -> list[str]:
    """The http(s) addresses of the links in a snapshot, as absolute addresses."""
    out = []
    for href in _LINK_LINE.findall(text):
        absolute = urljoin(base, href) if base else href
        if _WEB_ADDRESS.match(absolute):
            out.append(absolute)
    return out


def _sections(text: str) -> dict[str, str]:
    """The sections of a stretch of result text, the first of each title winning."""
    marks = list(_HEADER.finditer(text))
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        out.setdefault(m.group(1).strip(), text[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(text))].strip("\n"))
    return out


def _parse_result(text: str) -> tuple[dict[str, str], str | None]:
    """(the sections, the modal state's text) of one result, safe from headers a page forged inside a dialog's
    message. Result, Error, Open tabs and Page come before the modal state, so they are read from the text up to
    it; the real Snapshot and Events come after it, so they are read from the text after the last Snapshot header."""
    snapshot = [m for m in _HEADER.finditer(text) if m.group(1).strip() == "Snapshot"]
    cut = snapshot[-1].start() if snapshot else len(text)
    head, tail = text[:cut], text[cut:]
    modal = next((m for m in _HEADER.finditer(head) if m.group(1).strip() == "Modal state"), None)
    sections = _sections(head[:modal.start()] if modal else head)
    sections.update(_sections(tail))
    return sections, (head[modal.end():].strip("\n") if modal else None)


def _shrink(image_b64: str) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(base64.b64decode(image_b64))) as im:
        im.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 4))
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=70)
        return out.getvalue()


def _is_dns_name(host: str) -> bool:
    """A host that is a plain DNS name (not a number in disguise): labels of letters, digits and hyphens, and a last
    label that isn't all digits or 0x…, which a browser would read as an IPv4 address."""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    labels = host.rstrip(".").split(".")
    return bool(labels) and all(_DNS_LABEL.match(label) for label in labels) and not _NUMERIC_LABEL.match(labels[-1])


class Browser:
    def __init__(self, thumbnails=lambda: False, out_dir: Path | None = None, profile_dir: Path | None = None,
                 popen=subprocess.Popen, find_edge=find_edge, clock=time.monotonic):
        self.thumbnails = thumbnails  # whether to keep a replay picture after each step that changes a page
        self.out_dir = out_dir if out_dir is not None else aethel_home() / "browser" / "out"
        self.profile_dir = profile_dir if profile_dir is not None else aethel_home() / "browser" / "profile"
        self.popen, self.find_edge, self.clock = popen, find_edge, clock
        self._sign_in = None  # the Edge window the user was given to sign in with, while it's open
        self._sign_in_at = 0.0
        self.hub = None
        self._url: str | None = None                  # the page the model last saw
        self._title: str | None = None
        self._dialog: str | None = None               # the dialog waiting for an answer, as the server described it
        self._elements: dict[str, PageElement] = {}   # ...and its elements by ref

    def forget(self) -> None:
        """The browser restarted or its data was cleared: whatever page it was on isn't there any more."""
        self._url = self._title = self._dialog = None
        self._elements = {}

    # ---- signing in: the user's own window on the same profile --------------------------------------
    def signing_in(self) -> bool:
        return (self._sign_in is not None and self._sign_in.poll() is None
                and self.clock() - self._sign_in_at < SIGN_IN_MAX_SECONDS)

    def open_sign_in(self) -> bool:
        """Open Edge, visible, on Aethel's profile so the user logs in themselves. False if Edge can't be found.
        It doesn't keep running in the background after its window closes, and it doesn't sync the profile."""
        exe = self.find_edge()
        if exe is None:
            return False
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        self._sign_in = self.popen(
            [exe, f"--user-data-dir={self.profile_dir}", "--no-first-run", "--no-default-browser-check", "--disable-sync",
             "--disable-background-mode", "about:blank"], close_fds=True, creationflags=flags)
        self._sign_in_at = self.clock()
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

    def _digest(self, text: str) -> tuple[str, str | None, str | None, str | None, str | None]:
        """(what the model sees, the page's URL, its title, its snapshot YAML, a dialog waiting) for one result: the
        page and what it holds, not Playwright's echo of the code it ran or its console-log paths."""
        sections, modal = _parse_result(text)
        snap_body = sections.get("Snapshot", "")
        fence = _FENCE.search(snap_body)
        link = _SNAPSHOT_LINK.search(snap_body)
        yaml = fence.group(1) if fence else (self._snapshot_file(link.group(1)) if link else None)
        page = sections.get("Page", "")
        url_m, title_m = _PAGE_URL.search(page), _PAGE_TITLE.search(page)
        url = (url_m.group(1) or url_m.group(2)) if url_m else None
        title = title_m.group(1).strip() if title_m else None
        dialog = None
        if modal is not None and (m := _MODAL.search(modal)):
            dialog = " ".join(m.group(1).split())[:300]
        parts = []
        if sections.get("Result"):
            parts.append(sections["Result"].strip())
        if url:
            parts.append(f"Page: {title} — {url}" if title else f"Page: {url}")
        if dialog:
            parts.append(f"Dialog: {dialog}")
        if yaml:
            lines = yaml.rstrip().split("\n")
            kept, size = [], 0
            for i, line in enumerate(lines):
                size += len(line) + 1
                if size > MAX_SNAPSHOT_CHARS:
                    kept.append(f"[snapshot cut: {len(lines) - i} more lines. Use browser_snapshot with a target to look "
                                "at one part of the page.]")
                    break
                kept.append(line)
            parts.append("\n".join(kept))
        elif link:
            parts.append("[the page snapshot was too large to show. Use browser_snapshot with a target to look at one "
                         "part of the page.]")
        return "\n".join(parts) or "Done.", url, title, yaml, dialog

    def _remember(self, url: str | None, title: str | None, yaml: str | None, dialog: str | None, partial: bool = False) -> None:
        """The page as the model last saw it. Refs belong to one snapshot, so a whole new snapshot replaces the last;
        a snapshot of one part of the page (browser_snapshot with a target) adds to it."""
        self._dialog = dialog
        if url:
            self._url, self._title = url, title
        if yaml is None:
            return
        found = parse_page(f"- Page URL: {url}\n{yaml}" if url else yaml)[1]
        if partial:
            self._elements.update({e.ref: e for e in found})
        else:
            self._elements = {e.ref: e for e in found}

    def _scope_host(self) -> str:
        return host_of(self._url or "") or "browser"

    # ---- risk -------------------------------------------------------------------------------------
    def _named(self, args: dict) -> tuple[PageElement | None, list[str]]:
        """The element a call targets (from the page the model saw) and every name that describes it."""
        el = self._elements.get(str(args.get("target") or ""))
        names = [el.name] if el else []
        if args.get("element"):
            names.append(str(args["element"]))
        return el, names

    async def _evaluate_true(self, args: dict) -> bool | None:
        """True/False from the page itself (a fixed browser_evaluate); None when it couldn't be asked."""
        result = await self.hub.call_raw(SERVER, "browser_evaluate", args)
        if isinstance(result, str) or result.isError:
            return None
        text = "\n".join(c.text for c in result.content if isinstance(c, mt.TextContent))
        answer = re.search(r"### Result\s*\n\s*(true|false)\b", text)
        return None if answer is None else answer.group(1) == "true"

    async def _credential_field(self, remote: str, args: dict) -> str:
        """Whether this call would type into a password, code or card field: "yes" (by what the field is called, or
        what the page says it is), "no", or "unsure" (the page couldn't be asked)."""
        if remote == "browser_press_key":
            if str(args.get("key") or "") in NAVIGATION_KEYS:
                return "no"
            answer = await self._evaluate_true({"function": FOCUSED_CREDENTIAL_JS})
            return "unsure" if answer is None else ("yes" if answer else "no")
        if remote == "browser_fill_form":
            fields = [f for f in args.get("fields") or [] if isinstance(f, dict)]
            probes = [(f, [str(f.get("name") or ""), *self._named({"target": f.get("target"), "element": f.get("element")})[1]])
                      for f in fields]
        else:
            probes = [(args, self._named(args)[1])]
        unsure = False
        for field, names in probes:
            if any(CREDENTIAL_RE.search(n) for n in names):
                return "yes"
            answer = await self._evaluate_true({"target": str(field.get("target") or ""), "function": CREDENTIAL_JS})
            if answer:
                return "yes"
            unsure = unsure or answer is None
        return "unsure" if unsure else "no"

    async def _assess_navigation(self, url: str, what: str) -> Assessment:
        parts = _split(url)
        host = parts.hostname if parts is not None else None
        if not _WEB_ADDRESS.match(url) or parts is None or parts.scheme not in ("http", "https") or not host:
            return Assessment("deny", "Only plain http:// or https:// pages can be opened in the browser.", url or "(no address)")
        try:
            is_ip = bool(ipaddress.ip_address(host))
        except ValueError:
            is_ip = False
        if not is_ip and not _is_dns_name(host):  # "127.1", "0x7f.1", "2130706433": a browser reads these as IPv4
            return Assessment("deny", "That address isn't written as a normal web address.", url)
        try:
            await anyio.to_thread.run_sync(check_url, url, abandon_on_cancel=True)
        except BlockedAddress:
            return Assessment("deny", "That address is this computer or a private network, not the public web.", url)
        except socket.gaierror:
            pass  # a name that doesn't exist: the browser will say so
        return Assessment("allow", "", f"{what} {url}")

    def _consequential_names(self, remote: str, args: dict) -> list[str]:
        """What to read for 'does this send, buy or delete something': the element, and for a keystroke or a
        submit that has no element to read, the page it happens on; for a dialog, what it says."""
        el, names = self._named(args)
        if remote == "browser_handle_dialog":
            return [self._dialog] if self._dialog and args.get("accept", True) else []
        submits = (remote == "browser_press_key" and str(args.get("key") or "") == "Enter") or \
                  (remote == "browser_type" and args.get("submit"))
        if submits:
            parts = _split(self._url or "")
            names = [*names, self._title or "", *re.split(r"[^a-z0-9]+", (parts.path if parts else "").lower())]
        elif remote not in ("browser_click", "browser_select_option"):
            return []
        return names

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
            if remote == "browser_snapshot":
                return Assessment("allow", "", f"Look at {where}")
            if remote in ELEMENT_TOOLS or remote == "browser_fill_form":
                targets = [f.get("target") for f in args.get("fields") or [] if isinstance(f, dict)] \
                    if remote == "browser_fill_form" else [args.get("target")]
                if not targets or any(not isinstance(t, str) or not _REF.match(t) for t in targets):
                    return Assessment("deny", NOT_A_REF, f"{remote} without a snapshot ref")
            el, _ = self._named(args)
            what = f"“{el.name}”" if el else (f"“{args['element']}”" if args.get("element") else "")
            if remote in ("browser_type", "browser_fill_form", "browser_press_key"):
                verdict = await self._credential_field(remote, args)
                if verdict == "yes":
                    return Assessment("deny", REFUSED_CREDENTIALS, f"Type into {what or 'a field'} on {where}")
            else:
                verdict = "no"
            if remote == "browser_type":
                text = str(args.get("text") or "")
                target = f"Type “{text[:60]}{'…' if len(text) > 60 else ''}” into {what or 'a field'} on {where}"
            elif remote == "browser_handle_dialog":
                target = f"Answer the dialog “{(self._dialog or 'on the page')[:100]}” on {where}"
            else:
                verb = {"browser_click": "Click", "browser_hover": "Hover over", "browser_select_option": "Choose in",
                        "browser_fill_form": "Fill in a form on", "browser_press_key": f"Press {args.get('key', 'a key')} on"
                        }.get(remote, remote)
                target = f"{verb} {what} on {where}".replace("  ", " ") if what else f"{verb} {where}"
            if verdict == "unsure":
                return Assessment("ask", UNSURE_CREDENTIALS, target)
            if any(IRREVERSIBLE_RE.search(n) for n in self._consequential_names(remote, args)):
                return Assessment("allow", "This looks like it sends, buys or deletes something.", target, "irreversible")
            return Assessment("allow", "", target)
        return assess

    def _scope(self, remote: str):
        def scope(args: dict) -> str:  # "Allow for this task" covers one site
            url = args.get("url") if remote in ("browser_navigate", "browser_tabs") else None
            return host_of(str(url or "")) or self._scope_host()
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

    async def _public(self, url: str) -> bool:
        """Whether the page the browser is on is on the public web. A redirect, a clicked link or a popup can lead
        anywhere, and the server only blocks the origins it was told about."""
        parts = _split(url)
        if parts is None or parts.scheme not in ("http", "https"):
            return True  # about:blank, an error page: nothing there to show
        try:
            await anyio.to_thread.run_sync(check_url, url, abandon_on_cancel=True)
        except BlockedAddress:
            return False
        except socket.gaierror:
            pass
        return True

    def _tool(self, remote: mt.Tool) -> Tool:
        name, tier = remote.name, EXPOSED[remote.name]
        allowed = ALLOWED_ARGS[name]
        schema = dict(remote.inputSchema or {"type": "object"})
        schema["properties"] = {k: v for k, v in (schema.get("properties") or {}).items() if k in allowed}
        if "required" in schema:
            schema["required"] = [r for r in schema["required"] if r in allowed]

        async def handler(args: dict, ctx: ToolContext | None) -> ToolResult:
            if self.signing_in():  # also for a call made straight to the tool (the macro's snapshot)
                return ToolResult(False, "You have a browser window open for signing in. Close it, then ask me again.")
            args = {k: v for k, v in args.items() if k in allowed}  # never an argument nobody vetted
            el, _ = self._named(args) if name in ELEMENT_TOOLS else (None, [])  # the page as it was before the call
            raw = await self.hub.call_raw(SERVER, name, args)
            if isinstance(raw, str):
                return ToolResult(False, raw)
            text = "\n".join(c.text for c in raw.content if isinstance(c, mt.TextContent)).strip()
            if raw.isError:
                err = _sections(text).get("Error", text).split("Call log:")[0].strip()  # the log is noise to the model
                err = re.sub(r"^Error:\s*", "", err)
                return ToolResult(False, (err or "The browser reported an error.")[:1500], untrusted=True)
            content, url, title, yaml, dialog = self._digest(text)
            if url and not await self._public(url):
                await self.hub.call_raw(SERVER, "browser_navigate", {"url": "about:blank"})
                self.forget()
                return ToolResult(False, "That page is on this computer or a private network, so I don't show it. I took "
                                         "the browser back to a blank page.", untrusted=True)
            self._remember(url, title, yaml, dialog, partial=name == "browser_snapshot" and bool(args.get("target")))
            if yaml and ctx is not None and isinstance(ctx.sources, SourceList):
                ctx.sources.list_links(page_links(yaml, url))
            meta = {"app": "browser"}
            if el is not None:
                meta["element"] = {"role": el.role, "name": el.name, "window": el.window}
            result = ToolResult(True, content, untrusted=True, meta=meta, stateless=name in PAGE_TOOLS and yaml is None)
            if tier == "write" and self.thumbnails():
                result.thumbnail = await self._picture()
            return result

        return Tool(name, remote.description or name, schema, tier, handler,
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
    for stale in out.iterdir():  # snapshots and screenshots of earlier pages aren't kept around between runs
        with suppress(OSError):  # a file an orphaned Edge still holds open must not stop Aethel from starting
            shutil.rmtree(stale) if stale.is_dir() else stale.unlink(missing_ok=True)
    browser.out_dir, browser.profile_dir = out, profile
    browser.forget()
    hosts = ("127.0.0.1", "localhost", "[::1]")
    # every local port (the wildcard form), and the ports we know are Aethel's own
    blocked = ";".join([f"{scheme}://{host}:*" for scheme in ("http", "https") for host in hosts]
                       + [f"http://{host}:{port}" for port in [backend_port, *llama_ports] for host in hosts[:2]])
    cmd = [npx, "--prefer-offline", "-y", PACKAGE, "--browser", "msedge", "--user-data-dir", str(profile),
           "--output-dir", str(out), "--output-max-size", str(OUTPUT_MAX_BYTES), "--file-paths", "absolute",
           "--idle-timeout", str(IDLE_TIMEOUT_MS), "--no-webmcp", "--blocked-origins", blocked]
    if not show:
        cmd.append("--headless")
    return ServerSpec(SERVER, cmd, browser.adapt)  # no telemetry setting exists to turn off: see the probe notes above
