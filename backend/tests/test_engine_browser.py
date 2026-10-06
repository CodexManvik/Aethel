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

        return Tool(name, f"{name}.", {"type": "object"}, "read", run, lambda a: Assessment("allow", "", name),
                    toolgroup=group)

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


async def run(h, engine):  # noqa: F811
    task_id = await engine.start(conversation_id=h.convs.create().id, goal="look something up")
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
