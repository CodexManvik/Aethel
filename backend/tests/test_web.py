import socket

import httpx
import pytest

from aethel.tools import web
from aethel.tools.base import ToolContext

pytestmark = pytest.mark.anyio

PUBLIC = "93.184.216.34"


def resolve_to(monkeypatch, table=None, default=PUBLIC):
    """DNS never leaves the test: a host resolves to what `table` says, else to a public address."""
    table = table or {}

    def fake(host, port, *a, **k):
        ips = table.get(host, [default])
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))
                for ip in ips]

    monkeypatch.setattr(web.socket, "getaddrinfo", fake)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    resolve_to(monkeypatch)


def make_tools(handler, sources=None):
    seen = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    sources = sources or web.SourceList()
    tools = {t.name: t for t in web.web_tools(lambda ctx: sources, client)}
    return tools, sources, seen


async def call(tool, **args):
    return await tool.handler(args, ToolContext(task_id=None))


def html(title, body):
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                          text=f"<html><head><title>{title}</title></head><body><article><p>{body}</p></article></body></html>")


# ---- the guard -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "file:///c:/x", "ftp://example.com/x", "javascript:alert(1)", "http://10.0.0.5/", "http://[::1]/",
    "http://127.0.0.1:8765/api", "http://localhost/", "http://169.254.169.254/latest/meta-data",
    "http://0.0.0.0/", "http://192.168.1.2/", "http://172.16.0.1/", "http://100.64.0.1/",
    "http://[::ffff:127.0.0.1]/", "http://224.0.0.1/", "https:///nohost", "not a url",
])
def test_addresses_that_are_not_the_public_web_are_blocked(url, monkeypatch):
    resolve_to(monkeypatch, {"localhost": ["127.0.0.1", "::1"]})
    with pytest.raises(web.BlockedAddress):
        web.check_url(url)


def test_a_name_that_resolves_to_a_private_address_is_blocked_even_if_another_result_is_public(monkeypatch):
    resolve_to(monkeypatch, {"intranet.test": ["192.168.1.2"], "mixed.test": [PUBLIC, "10.1.1.1"]})
    for host in ("intranet.test", "mixed.test"):
        with pytest.raises(web.BlockedAddress):
            web.check_url(f"https://{host}/")
    web.check_url("https://example.com/path?q=1")  # a public name passes
    web.check_url("http://93.184.216.34:8080/")


# ---- web_search ------------------------------------------------------------------------------------------
async def test_search_numbers_results_and_keeps_a_urls_number(monkeypatch):
    results = iter([
        [{"title": "Rain in Paris", "href": "https://a.example/rain", "body": "It rains  a lot\nin April."},
         {"title": "Weather", "href": "https://b.example/w", "body": "Forecast."}],
        [{"title": "Rain in Paris (again)", "href": "https://a.example/rain", "body": "Same page."},
         {"title": "New", "href": "https://c.example/n", "body": ""}],
    ])
    monkeypatch.setattr(web, "_search", lambda query, n: next(results))
    tools, sources, _ = make_tools(lambda r: httpx.Response(500))
    first = await call(tools["web_search"], query="paris rain")
    assert first.ok and first.untrusted
    assert first.content == ("[1] Rain in Paris — https://a.example/rain\n    It rains a lot in April.\n"
                             "[2] Weather — https://b.example/w\n    Forecast.")
    second = await call(tools["web_search"], query="paris rain again", max_results=2)
    assert second.content.splitlines()[0] == "[1] Rain in Paris (again) — https://a.example/rain"  # same URL, same n
    assert second.content.splitlines()[2].startswith("[3] New — https://c.example/n")
    assert [(s.n, s.title, s.url) for s in sources.all()] == [
        (1, "Rain in Paris", "https://a.example/rain"), (2, "Weather", "https://b.example/w"),
        (3, "New", "https://c.example/n")]


async def test_a_result_whose_address_isnt_a_plain_web_address_is_dropped(monkeypatch):
    """Source URLs end up in a markdown link and an href: a hostile result mustn't smuggle markdown or a script."""
    monkeypatch.setattr(web, "_search", lambda q, n: [
        {"title": "Evil", "href": "javascript:alert(1)", "body": "x"},
        {"title": "Newline", "href": "https://a.example/x\n\n# injected heading", "body": "x"},
        {"title": "Space", "href": "https://a.example/a b", "body": "x"},
        {"title": "Angle", "href": "https://a.example/<script>", "body": "x"},
        {"title": "Quote", "href": 'https://a.example/"onmouseover=1', "body": "x"},
        {"title": "Fine", "href": "https://ok.example/page?q=1&r=(2)", "body": "ok"}])
    tools, sources, _ = make_tools(lambda r: httpx.Response(500))
    result = await call(tools["web_search"], query="x")
    assert result.content == "[1] Fine — https://ok.example/page?q=1&r=(2)\n    ok"
    assert [s.url for s in sources.all()] == ["https://ok.example/page?q=1&r=(2)"]


async def test_reading_an_address_with_whitespace_or_markup_is_refused_before_any_request():
    tools, sources, seen = make_tools(lambda r: html("T", "body"))
    for url in ("https://a.example/x\n\n# hi", "https://a.example/a b", 'https://a.example/"x', "https://a.example/<b>"):
        result = await call(tools["web_read"], url=url)
        assert not result.ok and "web address" in result.content
    assert seen == [] and sources.all() == []


async def test_search_limits_results_and_survives_a_failing_or_empty_search(monkeypatch):
    asked = []
    monkeypatch.setattr(web, "_search", lambda q, n: asked.append(n) or [])
    tools, _, _ = make_tools(lambda r: httpx.Response(500))
    assert (await call(tools["web_search"], query="x", max_results=99)).content == "No results."
    await call(tools["web_search"], query="x")
    await call(tools["web_search"], query="x", max_results=0)
    assert asked == [8, 5, 1]  # clamped to 1..8; 5 by default

    def boom(q, n):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(web, "_search", boom)
    failed = await call(tools["web_search"], query="x")
    assert not failed.ok and "rate limited" in failed.content
    empty = await call(tools["web_search"], query="   ")
    assert not empty.ok


async def test_search_gives_up_after_ten_seconds(monkeypatch):
    import time
    monkeypatch.setattr(web, "SEARCH_TIMEOUT_S", 0.05)
    monkeypatch.setattr(web, "_search", lambda q, n: time.sleep(0.5) or [])
    tools, _, _ = make_tools(lambda r: httpx.Response(500))
    result = await call(tools["web_search"], query="slow")
    assert not result.ok and "too long" in result.content


# ---- web_read --------------------------------------------------------------------------------------------
async def test_read_returns_a_numbered_page_and_the_title():
    tools, sources, seen = make_tools(lambda r: html("The Big Story", "Something happened today in the city."))
    result = await call(tools["web_read"], url="https://news.example/story")
    assert result.ok and result.untrusted
    assert result.content.startswith("[1] The Big Story — https://news.example/story\n\n")
    assert "Something happened today in the city." in result.content
    assert [(s.n, s.url) for s in sources.all()] == [(1, "https://news.example/story")]
    assert seen == ["https://news.example/story"]


async def test_read_pages_through_long_text_with_a_more_marker():
    body = "".join(f"{i:05d} " for i in range(5000))  # 30,000 chars, each chunk distinguishable
    tools, sources, _ = make_tools(lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, text=body))
    first = await call(tools["web_read"], url="https://t.example/long.txt")
    head, _, text = first.content.partition("\n\n")
    assert head == "[1] long.txt — https://t.example/long.txt"  # no <title>: the file name stands in
    assert text.startswith("00000 ") and text.endswith("\n\n[more: call web_read with start=12000]")
    second = await call(tools["web_read"], url="https://t.example/long.txt", start=12000)
    chunk = second.content.partition("\n\n")[2]
    assert chunk.startswith(body[12000:12012]) and chunk.endswith("[more: call web_read with start=24000]")
    third = await call(tools["web_read"], url="https://t.example/long.txt", start=24000)
    assert "[more:" not in third.content and third.content.rstrip().endswith(body.rstrip()[-10:])
    beyond = await call(tools["web_read"], url="https://t.example/long.txt", start=99999)
    assert beyond.ok and "no more text" in beyond.content.lower()
    assert [s.n for s in sources.all()] == [1]  # one URL, one number across all four reads


async def test_a_redirect_to_this_computer_is_blocked_on_the_hop():
    def handler(request):
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8765/api/settings"})
        return httpx.Response(200, text="SECRET")  # would be the backend's answer

    tools, _, seen = make_tools(handler)
    result = await call(tools["web_read"], url="https://public.example/go")
    assert not result.ok and "isn't the public web" in result.content
    assert seen == ["https://public.example/go"]  # the second hop was never requested


async def test_redirects_are_followed_a_few_hops_then_stop():
    def handler(request):
        n = int(request.url.path.strip("/") or 0)
        if n < 3:
            return httpx.Response(302, headers={"location": f"/{n + 1}"})  # a relative Location
        return html("Landed", "You made it.")

    tools, sources, seen = make_tools(handler)
    result = await call(tools["web_read"], url="https://hop.example/0")
    assert result.ok and "You made it." in result.content and len(seen) == 4
    assert result.content.startswith("[1] Landed — https://hop.example/0")  # numbered by the URL that was asked for

    endless = lambda request: httpx.Response(302, headers={"location": "/again"})  # noqa: E731
    tools, _, seen = make_tools(endless)
    looped = await call(tools["web_read"], url="https://loop.example/")
    assert not looped.ok and "redirect" in looped.content.lower() and len(seen) <= 6


async def test_a_huge_body_is_cut_at_two_megabytes_without_a_crash():
    big = "a" * 3_000_000
    tools, _, _ = make_tools(lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, text=big))
    result = await call(tools["web_read"], url="https://big.example/")
    assert result.ok and len(result.content) < 13_000 and "[more:" in result.content
    tail = await call(tools["web_read"], url="https://big.example/", start=1_995_000)
    text = tail.content.partition("\n\n")[2]
    assert len(text) == 5_000 and "[more:" not in text  # nothing past 2,000,000 characters


async def test_a_page_that_is_not_text_is_refused_and_errors_are_plain():
    tools, _, _ = make_tools(lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG"))
    refused = await call(tools["web_read"], url="https://img.example/a.png")
    assert not refused.ok and refused.content == "That page isn't text (image/png)."
    gone = make_tools(lambda r: httpx.Response(404, text="nope"))[0]
    result = await call(gone["web_read"], url="https://x.example/missing")
    assert not result.ok and "404" in result.content
    blocked = await call(gone["web_read"], url="http://10.0.0.5/admin")
    assert not blocked.ok and "isn't the public web" in blocked.content


async def test_a_search_result_and_its_page_share_a_source_number(monkeypatch):
    monkeypatch.setattr(web, "_search", lambda q, n: [{"title": "Story", "href": "https://news.example/story", "body": "s"}])
    tools, sources, _ = make_tools(lambda r: html("Story (full)", "The whole thing."))
    await call(tools["web_search"], query="story")
    page = await call(tools["web_read"], url="https://news.example/story")
    assert page.content.startswith("[1] ")
    assert len(sources.all()) == 1


# ---- the tool definitions --------------------------------------------------------------------------------
def test_the_tools_are_read_tier_web_group_and_check_the_scheme_before_asking():
    tools, _, _ = make_tools(lambda r: httpx.Response(500))
    for t in tools.values():
        assert (t.tier, t.toolgroup) == ("read", "web")
    assert tools["web_read"].assess({"url": "file:///c:/x"}).verdict == "deny"
    assert tools["web_read"].assess({"url": "https://a.example/x"}).verdict == "allow"
    assert tools["web_search"].assess({"query": "rain"}).target == 'Search the web for "rain"'
    assert tools["web_read"].parameters["required"] == ["url"] and tools["web_search"].parameters["required"] == ["query"]


def test_source_list_numbers_in_order_and_keeps_the_first_title():
    s = web.SourceList()
    assert (s.add("https://a", "A"), s.add("https://b", ""), s.add("https://a", "A2")) == (1, 2, 1)
    assert s.add("https://b", "Now titled") == 2
    assert [(x.n, x.title) for x in s.all()] == [(1, "A"), (2, "Now titled")]
