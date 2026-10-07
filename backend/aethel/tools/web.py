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
TITLE_CHARS = 200
MAX_MARKUP_CHARS = 1_000_000  # what is handed to the parser, however much was downloaded
HEAD_CHARS = 65_536           # a <title> further into the page than this isn't looked for
PARSE_TIMEOUT_S = 20.0
# identity: a gzip bomb would otherwise be inflated by the client before the size cap sees a byte of it
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0 Safari/537.36 Aethel", "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
           "Accept-Encoding": "identity"}
BLOCKED = "I can't open that address: it isn't the public web (it's this computer or a private network)."
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
# What may be read or listed as a source. Source URLs end up in a markdown link and an href, so anything that
# could break out of either (whitespace, control characters, quotes, angle brackets) is refused outright.
_WEB_ADDRESS = re.compile(r"^https?://[^\s<>\"\\\x00-\x1f\x7f-\x9f]+$", re.IGNORECASE)
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"\\\x00-\x1f\x7f-\x9f]+", re.IGNORECASE)
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


class BlockedAddress(Exception):
    """The URL isn't http(s), or its host is (or resolves to) an address that isn't on the public internet."""


def _public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):
        # an IPv4 address carried inside an IPv6 one is what it will reach: judge that one
        embedded = ip.ipv4_mapped or ip.sixtofour  # ::ffff:127.0.0.1, 2002:7f00:1::
        if embedded is None and any(ip in net for net in _NAT64):
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)  # 64:ff9b::7f00:1
        if embedded is not None:
            return _public(str(embedded))
        if ip.teredo is not None:
            return False  # a tunnel: where it ends up is not what the address says
    return ip.is_global and not (ip.is_multicast or ip.is_loopback or ip.is_link_local or ip.is_reserved
                                 or ip.is_unspecified or ip.is_private)


def _split(url: str):
    """urlsplit, or None when the address doesn't parse (a model can write anything: "http://[abc/x")."""
    try:
        return urlsplit(url.strip())
    except ValueError:
        return None


def host_of(url: str) -> str | None:
    """The site an address is on, without "www.", or None when it doesn't parse."""
    parts = _split(url)
    host = parts.hostname if parts is not None else None
    return host.removeprefix("www.") if host else None


def check_url(url: str) -> None:
    """Raises BlockedAddress unless `url` is http(s) and its host resolves only to public addresses.
    A name that doesn't resolve at all raises socket.gaierror (not an SSRF: the caller says "not found")."""
    parts = _split(url)
    try:
        host, port = (parts.hostname, parts.port) if parts is not None else (None, None)  # .port can raise too
    except ValueError:
        raise BlockedAddress(url) from None
    if parts is None or parts.scheme not in ("http", "https") or not host:
        raise BlockedAddress(url)
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:  # a name: every address it resolves to must be public
        port = port or (443 if parts.scheme == "https" else 80)
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
        self._user_urls: set[str] = set()
        self._listed: set[str] = set()  # addresses of links on a page the model was shown (not numbered sources)

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

    def seed(self, user_text: str) -> None:
        """Remember the web addresses the user themselves wrote: those are theirs to ask for."""
        self._user_urls |= {u.rstrip(".,;:!?)]}'") for u in _URL_IN_TEXT.findall(user_text)}

    def list_links(self, urls: list[str]) -> None:
        """Addresses of links on a page the model was shown: it may follow those exactly (a link can't carry what
        the model chose to put in it), but not an address it made up."""
        self._listed |= set(urls)

    def trusted(self, url: str) -> bool:
        """An address the user wrote, one that came up in a search or was read, or a link on a page shown to the
        model, this turn or task, exactly."""
        return url in self._user_urls or url in self._by_url or url in self._listed


def _flat(text: str, limit: int | None = None) -> str:
    text = " ".join(str(text or "").split())
    return text if limit is None or len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _search(query: str, n: int) -> list[dict]:
    from ddgs import DDGS  # imported on first use: it isn't needed unless the web is on
    return list(DDGS().text(query, max_results=n))


def _title_from(url: str) -> str:
    """No <title> to use: the file name, else the host."""
    parts = _split(url)
    last = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]) if parts is not None else ""
    return _flat(last or host_of(url) or url, TITLE_CHARS)


def _title_of(markup: str) -> str | None:
    """The page's <title>, found by plain searches in its first 64 KB (a regex over a hostile page can take
    minutes: unclosed tags make it backtrack). Capped, since it goes into the prompt and the UI."""
    head = markup[:HEAD_CHARS]
    low = head.lower()
    start = low.find("<title")
    opened = low.find(">", start) if start >= 0 else -1
    end = low.find("</title", opened) if opened >= 0 else -1
    return (_flat(htmllib.unescape(head[opened + 1:end]), TITLE_CHARS) or None) if end >= 0 else None


_CLOSERS = {"script": re.compile("</script", re.IGNORECASE), "style": re.compile("</style", re.IGNORECASE)}


def _strip_tags(markup: str) -> str:
    """The text of a page with its tags removed, in one pass: every search moves forward and never back, so a
    page of unclosed tags costs no more than a normal one. Script and style go, and so does an unclosed tag."""
    out, i = [], 0
    while True:
        j = markup.find("<", i)
        if j < 0:
            out.append(markup[i:])
            break
        out.append(markup[i:j])
        k = markup.find(">", j)
        if k < 0:
            break  # an unclosed tag: the rest of the page isn't text
        name = next((n for n in _CLOSERS if markup[j + 1:j + 1 + len(n)].lower() == n), None)
        if name:
            closer = _CLOSERS[name].search(markup, k)
            end = markup.find(">", closer.end()) if closer else -1
            if end < 0:
                break  # an unclosed script or style
            i = end + 1
        else:
            out.append(" ")
            i = k + 1
    text = htmllib.unescape("".join(out))
    return re.sub(r"[ \t]+", " ", text).strip()


def _page_text(markup: str) -> tuple[str, str | None]:
    """(readable text, <title>) of an HTML page, from at most MAX_MARKUP_CHARS of it."""
    import trafilatura
    markup = markup[:MAX_MARKUP_CHARS]
    try:
        text = trafilatura.extract(markup, include_links=False, favor_recall=True)
    except Exception:  # a parser that chokes on a page isn't a reason to lose the page
        text = None
    return text or _strip_tags(markup), _title_of(markup)


class _ReadError(Exception):
    pass


async def _fetch(http: httpx.AsyncClient, url: str) -> tuple[str, bytes, str]:
    """(content type, body up to 2 MB, charset). Redirects are followed by hand so each hop is checked."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            await anyio.to_thread.run_sync(check_url, current, abandon_on_cancel=True)
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


def unlisted_read(tool_name: str, args: dict, ctx: ToolContext) -> bool:
    """True when this opens an address nobody gave (web_read, or the browser's navigate / a new tab), asked for after
    outside content was read. A page can tell the model to open https://evil.example/?d=<what it knows>: opening it
    would carry that out. An address the user wrote, or one that came up in a search, was read, or is a link on a
    page the model was shown, is always fine."""
    if not ctx.tainted:
        return False
    if tool_name in ("web_read", "browser_navigate"):
        url = args.get("url")
    elif tool_name == "browser_tabs" and args.get("action") == "new":
        url = args.get("url")
    else:
        return False
    if tool_name == "browser_tabs" and not url:
        return False  # a blank new tab goes nowhere
    sources = ctx.sources
    return not (isinstance(sources, SourceList) and sources.trusted(str(url or "").strip()))


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
            if not _WEB_ADDRESS.match(url):
                continue  # not a plain web address: never listed, never numbered, never linked
            title = _flat(row.get("title"), TITLE_CHARS) or _title_from(url)
            lines.append(f"[{sources.add(url, title)}] {title} — {url}")
            if snippet := _flat(row.get("body"), SNIPPET_CHARS):
                lines.append(f"    {snippet}")
        return ToolResult(True, "\n".join(lines) or "No results.", untrusted=True)

    async def read(args: dict, ctx: ToolContext) -> ToolResult:
        url = str(args.get("url") or "").strip()
        if not _WEB_ADDRESS.match(url):
            return ToolResult(False, "That isn't a web address I can open (it must be a plain http:// or https:// link).")
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
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            return ToolResult(False, f"I couldn't load the page: {_flat(str(exc), 200) or type(exc).__name__}")
        try:
            markup = body.decode(charset, errors="replace")
        except LookupError:  # a charset Python has never heard of
            markup = body.decode("utf-8", errors="replace")
        if ctype == "text/plain":
            text, title = markup, None
        else:
            try:
                with anyio.fail_after(PARSE_TIMEOUT_S):
                    text, title = await anyio.to_thread.run_sync(_page_text, markup, abandon_on_cancel=True)
            except TimeoutError:
                return ToolResult(False, "That page took too long to read.")
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
        parts = _split(url)
        if parts is None or parts.scheme not in ("http", "https"):
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
             "read", read, assess_read, group="web", toolgroup="web",
             grant_scope=lambda args: host_of(str(args.get("url") or "")) or "web"),  # "Allow" covers one site
    ]
