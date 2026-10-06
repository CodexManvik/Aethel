"""Replaying compiled macros inside the engine (spec §6.3 tier 3, step 4)."""
import pytest

from aethel.memory.rsm import KnowledgeStore
from aethel.tools.base import Assessment, Tool, ToolResult
from tests.fakes import tool_call
from tests.test_engine import h, until  # noqa: F401  (h is a fixture)
from tests.test_rsm import fake_embed

pytestmark = pytest.mark.anyio

SNAP = ('window "YouTube - Mozilla Firefox"\n'
        '├── (640,60) edit "Search or enter address"  [action: type]\n'
        '└── (400,300) link "Lofi hip hop radio"  [action: click]')
MACRO = {"template": "play {p1} on YouTube in Firefox", "params": ["p1"], "steps": [
    {"tool": "win_app", "args": {"mode": "launch", "name": "firefox"}},
    {"tool": "win_type", "args": {"text": "youtube.com/results?search_query={p1+}", "press_enter": True},
     "target": {"role": "edit", "name": "Search or enter address", "window": "YouTube - Mozilla Firefox"}},
    {"tool": "win_click", "args": {}, "target": {"role": "link", "name": "Lofi hip hop radio",
                                                "window": "YouTube - Mozilla Firefox"}},
]}
DESKTOP = ("win_snapshot", "win_app", "win_type", "win_click")
REFLECT_ONLY = [[tool_call("record_learning", app_notes=[], skill=None)]]


class MacroS1:
    def __init__(self, skill_id, ground=None):
        self.skill_id, self.ground = skill_id, ground

    async def choice(self, state, instructions, options, purpose):
        if purpose == "skill_select":
            return self.skill_id, {k: (0.9 if k == self.skill_id else 0.05) for k in options}, 0.8
        pick, p = self.ground
        return pick, {k: (p if k == pick else (1 - p) / (len(options) - 1)) for k in options}, 0.5


@pytest.fixture
def macro_engine(h, tmp_path):  # noqa: F811
    made = []

    def make(turns, ground=None, snapshot=SNAP, ask=()):
        engine, provider = h.make(turns)
        engine.knowledge = KnowledgeStore(tmp_path / "k", fake_embed)
        skill, _ = engine.knowledge.upsert_skill(
            {"title": "Play a YouTube video in Firefox", "apps": ["firefox"],
             "intent": "Play a video on YouTube matching a search query",
             "steps": ["Open Firefox", "Search", "Open the first result"]}, "approved")
        engine.knowledge.set_macro(skill["id"], MACRO, "compiled")
        engine.system1 = MacroS1(skill["id"], ground)
        calls = []

        def fake(name):
            async def handler(args, ctx):
                calls.append((name, args))
                return ToolResult(True, snapshot if name == "win_snapshot" else "ok", untrusted=True)
            verdict = "ask" if name in ask else "allow"
            return Tool(name, name, {"type": "object"}, "read" if name == "win_snapshot" else "write", handler,
                        lambda a: Assessment(verdict, "", name))

        for name in DESKTOP:
            engine.registry.register(fake(name))
        made.append(engine)
        return engine, provider, skill, calls

    yield make
    for engine in made:
        for name in DESKTOP:
            engine.registry.unregister(name)


async def test_a_compiled_macro_replays_without_the_language_model(h, macro_engine):  # noqa: F811
    engine, provider, skill, calls = macro_engine(REFLECT_ONLY)
    conv = h.convs.create()
    task_id = await engine.start(conversation_id=conv.id, goal="play jazz piano on YouTube in Firefox")
    await engine.wait_idle()
    task = h.tasks.get(task_id)
    assert task.state == "done" and "steps I learned" in task.summary
    assert task.plan == ["Open firefox",
                         "Type “youtube.com/results?search_query=jazz+piano” into “Search or enter address”",
                         "Click “Lofi hip hop radio”"]
    assert task.plan_done == [0, 1, 2]
    acting = [c for c in calls if c[0] != "win_snapshot"]
    assert acting == [("win_app", {"mode": "launch", "name": "firefox"}),
                      ("win_type", {"text": "youtube.com/results?search_query=jazz+piano", "press_enter": True,
                                    "loc": [640, 60]}),
                      ("win_click", {"loc": [400, 300]})]
    assert all(s.decider == "macro" for s in h.tasks.steps(task_id))
    assert len(provider.calls) == 1  # no planner, no executor: only the reflection afterwards
    assert engine.knowledge.get(skill["id"])["macro"] == "compiled"


async def test_a_renamed_element_is_grounded_by_system1(h, macro_engine):  # noqa: F811
    renamed = SNAP.replace("Lofi hip hop radio", "lofi hip hop radio - beats to relax")
    engine, _, _, calls = macro_engine(REFLECT_ONLY, ground=("e0", 0.9), snapshot=renamed)
    conv = h.convs.create()
    task_id = await engine.start(conversation_id=conv.id, goal="play lofi on YouTube in Firefox")
    await engine.wait_idle()
    assert h.tasks.get(task_id).state == "done" and ("win_click", {"loc": [400, 300]}) in calls


async def test_drift_hands_over_to_the_llm_and_the_macro_is_repaired(h, macro_engine):  # noqa: F811
    gone = SNAP.replace('└── (400,300) link "Lofi hip hop radio"  [action: click]', "")
    engine, provider, skill, _ = macro_engine([
        [tool_call("win_click", loc=[500, 320])],                    # the LLM carries on from the screen
        [tool_call("finish_task", summary="Playing it now.")],
        [tool_call("record_learning", app_notes=[], skill=None)],
    ], ground=("none", 0.95), snapshot=gone)
    conv = h.convs.create()
    task_id = await engine.start(conversation_id=conv.id, goal="play lofi on YouTube in Firefox")
    await engine.wait_idle()
    assert h.tasks.get(task_id).state == "done"
    handover = next(m.content for m in provider.calls[0] if m.role == "user" and "I started by replaying" in m.content)
    assert "- Open firefox" in handover and "couldn't find “Lofi hip hop radio”" in handover
    assert [s.decider for s in h.tasks.steps(task_id)] == ["macro", "macro", "agent"]
    # The LLM's click has no recorded element here, so the run can't be recompiled: a failed repair.
    assert engine.knowledge.get(skill["id"])["repairs_failed"] == 1


async def test_macro_steps_still_wait_for_approval(h, macro_engine):  # noqa: F811
    engine, _, _, calls = macro_engine(REFLECT_ONLY, ask=("win_type",))
    conv = h.convs.create()
    task_id = await engine.start(conversation_id=conv.id, goal="play lofi on YouTube in Firefox")
    needed = await until(h.events, lambda e: e["type"] == "approval_needed")
    assert needed["tool"] == "win_type" and not any(c[0] == "win_type" for c in calls)
    await engine.approvals.resolve(needed["approval_id"], "allow_once")
    await engine.wait_idle()
    assert h.tasks.get(task_id).state == "done"


async def test_a_goal_that_does_not_fit_the_template_is_planned_normally(h, macro_engine):  # noqa: F811
    engine, provider, _, calls = macro_engine([
        [tool_call("submit_plan", steps=["Open Firefox"], checks=[])],
        [tool_call("finish_task", summary="ok")],
        [tool_call("record_learning", app_notes=[], skill=None)],
    ])
    conv = h.convs.create()
    task_id = await engine.start(conversation_id=conv.id, goal="open firefox settings")
    await engine.wait_idle()
    assert h.tasks.get(task_id).plan == ["Open Firefox"] and calls == []


# ---- browser macros (Phase 3 spec §7.6) -----------------------------------------------------------------------------
PAGE = ('Page: Bing — https://www.bing.com/\n'
        '- generic [active] [ref=e1]:\n'
        '  - textbox "Search" [ref=e5]\n'
        '  - link "Opening hours" [ref=e21]')
BROWSER_MACRO = {"template": "search Bing for {p1}", "params": ["p1"], "steps": [
    {"tool": "browser_navigate", "args": {"url": "https://www.bing.com/"}},
    {"tool": "browser_type", "args": {"text": "{p1}", "submit": True},
     "target": {"role": "textbox", "name": "Search", "window": "www.bing.com"}},
    {"tool": "browser_click", "args": {}, "target": {"role": "link", "name": "Opening hours", "window": "www.bing.com"}},
]}
BROWSER = ("browser_snapshot", "browser_navigate", "browser_type", "browser_click")


@pytest.fixture
def browser_engine(h, tmp_path):  # noqa: F811
    made = []

    def make(turns, ground=None, page=PAGE, connected=True):
        engine, provider = h.make(turns)
        engine.knowledge = KnowledgeStore(tmp_path / "k", fake_embed)
        skill, _ = engine.knowledge.upsert_skill(
            {"title": "Search Bing for something", "apps": ["browser"], "intent": "Search the web with Bing for a query",
             "steps": ["Open Bing", "Search", "Open the first result"]}, "approved")
        engine.knowledge.set_macro(skill["id"], BROWSER_MACRO, "compiled")
        engine.system1 = MacroS1(skill["id"], ground)
        calls = []

        def fake(name):
            async def handler(args, ctx):
                calls.append((name, dict(args)))
                return ToolResult(True, page if name == "browser_snapshot" else "ok", untrusted=True)
            return Tool(name, name, {"type": "object"}, "read" if name in ("browser_snapshot", "browser_navigate") else "write",
                        handler, lambda a: Assessment("allow", "", name), toolgroup="browser")

        for name in BROWSER if connected else BROWSER[1:]:
            engine.registry.register(fake(name))
        made.append(engine)
        return engine, provider, skill, calls

    yield make
    for engine in made:
        for name in BROWSER:
            engine.registry.unregister(name)


async def test_a_browser_macro_replays_by_finding_each_element_on_the_live_page(h, browser_engine):  # noqa: F811
    engine, provider, skill, calls = browser_engine(REFLECT_ONLY)
    conv = h.convs.create()
    task_id = await engine.start(conversation_id=conv.id, goal="search Bing for jazz piano lessons")
    await engine.wait_idle()
    task = h.tasks.get(task_id)
    assert task.state == "done" and "steps I learned" in task.summary
    assert task.plan == ["Go to https://www.bing.com/", "Type “jazz piano lessons” into “Search”", "Click “Opening hours”"]
    acting = [c for c in calls if c[0] != "browser_snapshot"]
    assert acting == [("browser_navigate", {"url": "https://www.bing.com/"}),
                      ("browser_type", {"text": "jazz piano lessons", "submit": True, "target": "e5", "element": "Search"}),
                      ("browser_click", {"target": "e21", "element": "Opening hours"})]   # refs come from the fresh snapshot
    assert [c[0] for c in calls].count("browser_snapshot") == 2                            # one before each targeted step
    assert all(s.decider == "macro" for s in h.tasks.steps(task_id))
    assert len(provider.calls) == 1                                                        # only the reflection afterwards
    assert not h.tasks.get(task_id).error


async def test_a_page_that_looks_different_is_grounded_by_system1_then_the_ref_is_the_new_one(h, browser_engine):  # noqa: F811
    renamed = PAGE.replace('link "Opening hours" [ref=e21]', 'link "Library opening hours and contact" [ref=e33]')
    engine, _, _, calls = browser_engine(REFLECT_ONLY, ground=("e0", 0.9), page=renamed)
    conv = h.convs.create()
    task_id = await engine.start(conversation_id=conv.id, goal="search Bing for leeds library")
    await engine.wait_idle()
    assert h.tasks.get(task_id).state == "done"
    assert ("browser_click", {"target": "e33", "element": "Library opening hours and contact"}) in calls


async def test_drift_on_a_page_hands_over_to_the_llm(h, browser_engine):  # noqa: F811
    gone = PAGE.replace('  - link "Opening hours" [ref=e21]', "")
    engine, provider, skill, _ = browser_engine([
        [tool_call("browser_click", target="e9")],
        [tool_call("finish_task", summary="Done.")],
        [tool_call("record_learning", app_notes=[], skill=None)],
    ], ground=("none", 0.95), page=gone)
    conv = h.convs.create()
    task_id = await engine.start(conversation_id=conv.id, goal="search Bing for leeds library")
    await engine.wait_idle()
    assert h.tasks.get(task_id).state == "done"
    handover = next(m.content for m in provider.calls[0] if m.role == "user" and "I started by replaying" in m.content)
    assert "- Go to https://www.bing.com/" in handover and "couldn't find “Opening hours”" in handover
    assert [s.decider for s in h.tasks.steps(task_id)] == ["macro", "macro", "agent"]


async def test_a_browser_macro_without_a_browser_hands_over_instead_of_failing(h, browser_engine):  # noqa: F811
    engine, provider, _, calls = browser_engine([
        [tool_call("finish_task", summary="I couldn't open the browser.")],
        [tool_call("record_learning", app_notes=[], skill=None)],
    ], connected=False)
    conv = h.convs.create()
    task_id = await engine.start(conversation_id=conv.id, goal="search Bing for leeds library")
    await engine.wait_idle()
    handover = next(m.content for m in provider.calls[0] if m.role == "user" and "I started by replaying" in m.content)
    assert "the browser isn't connected" in handover and "desktop control" not in handover
    assert h.tasks.get(task_id).state == "done"


async def test_the_snapshot_tool_is_never_logged_as_a_step_and_the_macro_still_waits_for_approval(h, browser_engine):  # noqa: F811
    engine, _, _, calls = browser_engine(REFLECT_ONLY)
    ask = Tool("browser_click", "click", {"type": "object"}, "write", engine.registry.get("browser_click").handler,
               lambda a: Assessment("ask", "", "click"), toolgroup="browser")
    engine.registry.unregister("browser_click")
    engine.registry.register(ask)
    conv = h.convs.create()
    task_id = await engine.start(conversation_id=conv.id, goal="search Bing for leeds library")
    needed = await until(h.events, lambda e: e["type"] == "approval_needed")
    assert needed["tool"] == "browser_click" and not any(c[0] == "browser_click" for c in calls)
    await engine.approvals.resolve(needed["approval_id"], "allow_once")
    await engine.wait_idle()
    assert [s.tool for s in h.tasks.steps(task_id)] == ["browser_navigate", "browser_type", "browser_click"]
