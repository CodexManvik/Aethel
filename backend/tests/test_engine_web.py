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
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, text="Paris sees rain in April.")))
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

    yield SimpleNamespace(h=h, make=make)
    for engine in made:
        for name in ("web_search", "web_read"):
            engine.registry.unregister(name)


async def run(engine, conv):
    task_id = await engine.start(conversation_id=conv.id, goal="find out about rain in Paris")
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
