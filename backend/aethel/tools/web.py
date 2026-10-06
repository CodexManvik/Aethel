"""Web search and page reading for chat and for tasks (Phase 3 spec §6).

Both tools are read-tier and everything they return is untrusted: it's wrapped, it taints a task, and it never
reaches fact extraction. Every page URL is checked before it is fetched, and again on every redirect hop, so a
page can't send Aethel to this computer or to a private network (SSRF). Each URL gets one number per turn or
task, the same in a search result and a read of that page, so an answer can cite [n].

One limit worth knowing: the name is resolved to check it and again to connect, so a hostile DNS server that
answers differently the second time could still slip through (DNS rebinding). Pinning the connection to the
checked address would close it; the guard doesn't do that yet."""
import html as htmllib
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import unquote, urljoin, urlsplit

import anyio
import httpx

from .base import Assessment, Tool, ToolContext, ToolResult

MAX_READ_CHARS = 12_000
MAX_BODY_BYTES = 2_000_000
MAX_REDIRECTS = 5
SEARCH_TIMEOUT_S = 10.0
FETCH_TIMEOUT_S = 15.0
DEFAULT_RESULTS, MAX_RESULTS = 5, 8
SNIPPET_CHARS = 300
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0 Safari/537.36 Aethel", "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1"}
BLOCKED = "I can't open that address: it isn't the public web (it's this computer or a private network)."
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


class BlockedAddress(Exception):
    """The URL isn't http(s), or its host is (or resolves to) an address that isn't on the public internet."""


def _public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is 127.0.0.1
    return ip.is_global and not (ip.is_multicast or ip.is_loopback or ip.is_link_local or ip.is_reserved
                                 or ip.is_unspecified or ip.is_private)


def check_url(url: str) -> None:
    """Raises BlockedAddress unless `url` is http(s) and its host resolves only to public addresses.
    A name that doesn't resolve at all raises socket.gaierror (not an SSRF: the caller says "not found")."""
    parts = urlsplit(url.strip())
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise BlockedAddress(url)
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:  # a name: every address it resolves to must be public
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    if not addresses or not all(_public(a) for a in addresses):
        raise BlockedAddress(url)


@dataclass
class Source:
    n: int
    title: str
    url: str


class SourceList:
    """The sources of one turn or task, numbered from 1 in the order they were met."""

    def __init__(self) -> None:
        self._by_url: dict[str, Source] = {}

    def add(self, url: str, title: str) -> int:
        """The same URL keeps its number; a title is kept once there is one."""
        found = self._by_url.get(url)
        if found is None:
            found = self._by_url[url] = Source(len(self._by_url) + 1, title, url)
        elif not found.title and title:
            found.title = title
        return found.n

    def all(self) -> list[Source]:
        return list(self._by_url.values())


def _flat(text: str, limit: int | None = None) -> str:
    text = " ".join(str(text or "").split())
    return text if limit is None or len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _search(query: str, n: int) -> list[dict]:
    from ddgs import DDGS  # imported on first use: it isn't needed unless the web is on
    return list(DDGS().text(query, max_results=n))


def _title_from(url: str) -> str:
    """No <title> to use: the file name, else the host."""
    parts = urlsplit(url)
    last = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    return last or parts.hostname or url


def _page_text(markup: str) -> tuple[str, str | None]:
    """(readable text, <title>) of an HTML page."""
    import trafilatura
    found = re.search(r"<title[^>]*>(.*?)</title>", markup, re.IGNORECASE | re.DOTALL)
    title = _flat(htmllib.unescape(found.group(1))) if found else None
    text = trafilatura.extract(markup, include_links=False, favor_recall=True)
    if not text:  # nothing article-like: whatever text the page has
        stripped = re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"[ \t]+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", stripped))).strip()
    return text, title or None


class _ReadError(Exception):
    pass


async def _fetch(http: httpx.AsyncClient, url: str) -> tuple[str, bytes, str]:
    """(content type, body up to 2 MB, charset). Redirects are followed by hand so each hop is checked."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            await anyio.to_thread.run_sync(check_url, current)
        except BlockedAddress:
            raise _ReadError(BLOCKED) from None
        except socket.gaierror:
            raise _ReadError("I couldn't find that website.") from None
        async with http.stream("GET", current, headers=HEADERS, follow_redirects=False,
                               timeout=FETCH_TIMEOUT_S) as resp:
            location = resp.headers.get("location")
            if resp.status_code in (301, 302, 303, 307, 308) and location:
                current = urljoin(current, location)
                continue
            if resp.status_code >= 400:
                raise _ReadError(f"The page returned {resp.status_code}.")
            ctype = resp.headers.get("content-type", "text/html").split(";")[0].strip().lower() or "text/html"
            if ctype not in TEXT_TYPES:
                raise _ReadError(f"That page isn't text ({ctype}).")
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) >= MAX_BODY_BYTES:
                    del body[MAX_BODY_BYTES:]
                    break
            return ctype, bytes(body), resp.charset_encoding or "utf-8"
    raise _ReadError("That page redirected too many times.")


def web_tools(sources_for: Callable[[ToolContext], SourceList], http: httpx.AsyncClient) -> list[Tool]:
    """web_search and web_read. `sources_for(ctx)` is the numbered source list of the turn or task asking."""

    async def search(args: dict, ctx: ToolContext) -> ToolResult:
        query = _flat(args.get("query"))
        if not query:
            return ToolResult(False, "Give me something to search for.")
        try:
            n = max(1, min(MAX_RESULTS, int(args.get("max_results", DEFAULT_RESULTS))))
        except (TypeError, ValueError):
            n = DEFAULT_RESULTS
        try:
            with anyio.fail_after(SEARCH_TIMEOUT_S):
                rows = await anyio.to_thread.run_sync(_search, query, n, abandon_on_cancel=True)
        except TimeoutError:
            return ToolResult(False, "The search took too long. Try again, or try other words.")
        except Exception as exc:  # the search library raises its own errors (rate limits, network)
            return ToolResult(False, f"The search failed: {_flat(str(exc), 200) or type(exc).__name__}")
        sources, lines = sources_for(ctx), []
        for row in rows:
            url = str(row.get("href") or row.get("url") or "").strip()
            if not url:
                continue
            title = _flat(row.get("title")) or _title_from(url)
            lines.append(f"[{sources.add(url, title)}] {title} — {url}")
            if snippet := _flat(row.get("body"), SNIPPET_CHARS):
                lines.append(f"    {snippet}")
        return ToolResult(True, "\n".join(lines) or "No results.", untrusted=True)

    async def read(args: dict, ctx: ToolContext) -> ToolResult:
        url = str(args.get("url") or "").strip()
        try:
            start = max(0, int(args.get("start") or 0))
        except (TypeError, ValueError):
            start = 0
        try:
            with anyio.fail_after(FETCH_TIMEOUT_S * 2):
                ctype, body, charset = await _fetch(http, url)
        except _ReadError as exc:
            return ToolResult(False, str(exc))
        except TimeoutError:
            return ToolResult(False, "The page took too long to load.")
        except httpx.HTTPError as exc:
            return ToolResult(False, f"I couldn't load the page: {_flat(str(exc), 200) or type(exc).__name__}")
        markup = body.decode(charset, errors="replace") if charset else body.decode("utf-8", errors="replace")
        if ctype == "text/plain":
            text, title = markup, None
        else:
            text, title = await anyio.to_thread.run_sync(_page_text, markup)
        title = title or _title_from(url)
        head = f"[{sources_for(ctx).add(url, title)}] {title} — {url}\n\n"
        if not text.strip():
            return ToolResult(True, head + "(The page has no readable text.)", untrusted=True)
        if start >= len(text):
            return ToolResult(True, head + "[no more text on this page]", untrusted=True)
        chunk = text[start:start + MAX_READ_CHARS]
        more = start + MAX_READ_CHARS < len(text)
        tail = f"\n\n[more: call web_read with start={start + MAX_READ_CHARS}]" if more else ""
        return ToolResult(True, head + chunk + tail, untrusted=True)

    def assess_search(args: dict) -> Assessment:
        return Assessment("allow", "", f'Search the web for "{_flat(args.get("query"), 80)}"')

    def assess_read(args: dict) -> Assessment:
        url = str(args.get("url") or "").strip()
        if urlsplit(url).scheme not in ("http", "https"):
            return Assessment("deny", "Only http:// or https:// pages can be read.", url or "(no url)")
        return Assessment("allow", "", f"Read {url}")  # where it points is checked when it is fetched

    return [
        Tool("web_search", "Search the web. Results are numbered [n]; cite them as [n] and never invent a source.",
             {"type": "object", "properties": {
                 "query": {"type": "string"},
                 "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS}},
              "required": ["query"]},
             "read", search, assess_search, group="web", toolgroup="web"),
        Tool("web_read", "Read a web page as text (up to 12,000 characters; start continues a long page).",
             {"type": "object", "properties": {
                 "url": {"type": "string", "description": "An http:// or https:// address"},
                 "start": {"type": "integer", "minimum": 0}},
              "required": ["url"]},
             "read", read, assess_read, group="web", toolgroup="web"),
    ]
