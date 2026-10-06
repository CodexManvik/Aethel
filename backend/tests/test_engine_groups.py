"""Tool groups on demand in the task engine (token spec §6). The fixtures come from test_engine."""
from types import SimpleNamespace

import pytest

from aethel.tools.base import Assessment, Tool, ToolResult
from tests.fakes import tool_call
from tests.test_engine import h, plan  # noqa: F401  (h is a fixture)

pytestmark = pytest.mark.anyio


def _grouped_registry(engine):
    """Beside the fs_* tools (core): a fake Word tool (office) and a fake search tool (files)."""
    calls = []

    def fake(name, group):
        async def run(args, ctx):
            calls.append(name)
            return ToolResult(True, f"{name} ran")

        return Tool(name, f"{name}.", {"type": "object"}, "read", run, lambda a: Assessment("allow", "", name),
                    toolgroup=group)

    engine.registry.register(fake("word_new", "office"))
    engine.registry.register(fake("dc_search", "files"))
    return calls


@pytest.fixture
def g(h):  # noqa: F811
    """Like h, but each test's engine also gets the fake grouped tools (removed again afterwards)."""
    made = []

    def make(turns, groups_on=True, **kw):
        h.settings.update({"token_saving": {"tool_groups": groups_on}})
        engine, provider = h.make(turns, settings=h.settings, **kw)
        made.append(engine)
        return engine, provider, _grouped_registry(engine)

    yield SimpleNamespace(h=h, make=make)
    for engine in made:
        for name in ("word_new", "dc_search"):
            engine.registry.unregister(name)


async def run_goal(h, engine):  # noqa: F811
    task_id = await engine.start(conversation_id=h.convs.create().id, goal="x")
    await engine.wait_idle()
    return task_id


async def test_groups_off_offers_every_tool_as_before_and_no_use_tools(g):
    engine, provider, _ = g.make([[plan(["Do it"])], [tool_call("finish_task", summary="Done.")]], groups_on=False)
    await run_goal(g.h, engine)
    execute = provider.tools_seen[1]
    assert {"fs_read", "word_new", "dc_search", "complete_plan_step", "finish_task"} <= set(execute)
    assert "use_tools" not in execute
    assert "More tools on request" not in provider.calls[1][0].content          # the system text is untouched
    assert "word_new" in provider.calls[0][1].content                           # the planner lists every tool


async def test_groups_on_starts_with_the_core_tools_and_a_catalogue(g):
    turns = [[plan(["Make a doc"])], [tool_call("use_tools", call_id="u1", group="office")],
             [tool_call("word_new", call_id="w1")], [tool_call("finish_task", summary="Done.")]]
    engine, provider, calls = g.make(turns)
    task_id = await run_goal(g.h, engine)
    first, second = provider.tools_seen[1], provider.tools_seen[2]
    assert "fs_read" in first and "use_tools" in first and "complete_plan_step" in first
    assert "word_new" not in first and "dc_search" not in first
    system = provider.calls[1][0].content
    assert "More tools on request (call use_tools): " in system and "office (" in system and "files (" in system
    assert "word_new" in second and "dc_search" not in second and "use_tools" in second   # files still on request
    later = provider.calls[2][0].content
    assert "files (" in later and "office (" not in later                                  # office: no longer on request
    assert later.split("More tools on request")[0] == system.split("More tools on request")[0]  # same prompt before it
    use = [m.content for m in provider.calls[2] if m.role == "tool"]
    assert use and use[0].startswith("Added office tools: word_new")
    assert calls == ["word_new"]
    assert [s.tool for s in g.h.tasks.steps(task_id)] == ["word_new"]                      # use_tools is not a step


async def test_the_catalogue_and_use_tools_disappear_once_every_group_is_active(g):
    turns = [[plan(["x"])], [tool_call("use_tools", call_id="a", group="office")],
             [tool_call("use_tools", call_id="b", group="files")], [tool_call("finish_task", summary="Done.")]]
    engine, provider, _ = g.make(turns)
    await run_goal(g.h, engine)
    assert "use_tools" in provider.tools_seen[2] and "use_tools" not in provider.tools_seen[3]
    assert "More tools on request" not in provider.calls[3][0].content


async def test_calling_a_tool_of_an_inactive_group_adds_the_group_and_runs_the_call(g):
    turns = [[plan(["Make a doc"])], [tool_call("word_new", call_id="w1")], [tool_call("finish_task", summary="Done.")]]
    engine, provider, calls = g.make(turns)
    task_id = await run_goal(g.h, engine)
    assert "word_new" not in provider.tools_seen[1]      # it wasn't offered: the model guessed the name
    assert calls == ["word_new"]                         # ...and it ran, through the normal path
    assert "word_new" in provider.tools_seen[2]          # ...and office is active from then on
    assert [s.tool for s in g.h.tasks.steps(task_id)] == ["word_new"]


async def test_an_unknown_group_gets_an_error_listing_the_ones_that_exist(g):
    turns = [[plan(["x"])], [tool_call("use_tools", call_id="u1", group="teleport")],
             [tool_call("finish_task", summary="Done.")]]
    engine, provider, _ = g.make(turns)
    await run_goal(g.h, engine)
    reply = next(m.content for m in provider.calls[2] if m.role == "tool")
    assert reply.startswith("Error") and "office" in reply and "files" in reply
    assert "word_new" not in provider.tools_seen[2]


async def test_asking_again_for_an_active_group_is_cheap_and_loop_guarded(g):
    turns = [[plan(["x"])]] + [[tool_call("use_tools", call_id=f"u{i}", group="office")] for i in range(5)] + \
            [[tool_call("finish_task", summary="Done.")]]
    engine, provider, _ = g.make(turns, max_identical_calls=2)
    await run_goal(g.h, engine)
    replies = [m.content for m in provider.calls[-1] if m.role == "tool"]
    assert replies[0].startswith("Added office tools")
    assert replies[1].startswith("The office tools are already available")
    assert any("[LOOP DETECTED]" in r for r in replies)


async def test_the_planner_can_name_the_groups_a_task_needs(g):
    turns = [[tool_call("submit_plan", steps=["Search the files"], checks=[], tool_groups=["files"])],
             [tool_call("dc_search", call_id="d1")], [tool_call("finish_task", summary="Done.")]]
    engine, provider, calls = g.make(turns)
    await run_goal(g.h, engine)
    prompt = provider.calls[0][1].content
    assert "fs_read" in prompt and "word_new" not in prompt and "dc_search" not in prompt   # core tools only
    assert "office (" in prompt and "files (" in prompt                                      # ...and the catalogue
    planner_spec = next(s for s in provider.specs_seen[0] if s.name == "submit_plan")
    assert planner_spec.parameters["properties"]["tool_groups"]["items"]["enum"] == ["office", "files"]
    assert "dc_search" in provider.tools_seen[1] and "word_new" not in provider.tools_seen[1]   # seeded from the plan
    assert "files (" not in provider.calls[1][0].content and "office (" in provider.calls[1][0].content
    assert calls == ["dc_search"]


async def test_the_planner_sees_the_plain_submit_plan_when_groups_are_off(g):
    engine, provider, _ = g.make([[plan(["Do it"])], [tool_call("finish_task", summary="Done.")]], groups_on=False)
    await run_goal(g.h, engine)
    from aethel.runtime.prompts import SUBMIT_PLAN
    assert next(s for s in provider.specs_seen[0] if s.name == "submit_plan") == SUBMIT_PLAN


async def test_a_resumed_task_and_a_learned_skill_bring_back_the_groups_they_need(g):
    turns = [[plan(["Make a doc"])], [tool_call("word_new", call_id="w1")], [tool_call("finish_task", summary="Done.")]]
    engine, provider, calls = g.make(turns)
    task_id = await run_goal(g.h, engine)
    assert engine._groups_for_steps(task_id) == {"office"}
    assert engine._skill_groups(["s1"]) == set()          # nothing learned: nothing extra
    engine.knowledge = SimpleNamespace(get=lambda i: {"apps": ["Word", "notepad"], "macro_def": {"steps": [
        {"tool": "dc_search"}, {"tool": "gone_tool"}]}})
    assert engine._skill_groups(["s1"]) == {"office", "files"}   # a skill about Word; a macro step on a files tool
