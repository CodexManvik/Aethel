from aethel.memory.rsm import KnowledgeStore
from aethel.runtime import macro
from aethel.runtime.reflect import maybe_compile
from aethel.runtime.store import StepRecord
from tests.test_rsm import fake_embed

STABLE = "Play a YouTube video in Firefox. Play a video on YouTube matching a search query. firefox"


def step(i, tool, args, element=None, ok=True):
    meta = {"app": "firefox", "element": {"role": element[0], "name": element[1], "window": "YouTube"}} if element else None
    return StepRecord(id=f"s{i}", task_id="t", idx=i, tool=tool, args=args, summary="", verdict="allow", ok=ok,
                      result="", duration_ms=10, created_at="", meta=meta)


def lofi_run(query="lofi"):
    return [
        step(0, "win_app", {"mode": "launch", "name": "firefox"}),
        step(1, "win_snapshot", {}),
        step(2, "win_click", {"loc": [640, 60]}, ("edit", "Search or enter address")),
        step(3, "win_type", {"loc": [640, 60], "text": f"youtube.com/results?search_query={query.replace(' ', '+')}",
                             "press_enter": True}, ("edit", "Search or enter address")),
        step(4, "win_wait", {"duration": 2}),
        step(5, "win_click", {"loc": [400, 300]}, ("link", "Lofi hip hop radio")),
        step(6, "fs_list", {"path": "C:/x"}),  # not a desktop step: never part of a macro
    ]


def test_a_navigation_run_compiles_with_the_query_as_a_parameter():
    m = macro.compile_macro("play lofi on YouTube in Firefox", lofi_run(), STABLE)
    assert m["template"] == "play {p1} on YouTube in Firefox" and m["params"] == ["p1"]
    tools = [s["tool"] for s in m["steps"]]
    assert tools == ["win_app", "win_click", "win_type", "win_wait", "win_click"]  # no snapshot, no fs step
    assert m["steps"][2]["args"] == {"text": "youtube.com/results?search_query={p1+}", "press_enter": True}
    assert m["steps"][1]["target"] == {"role": "edit", "name": "Search or enter address", "window": "YouTube"}
    assert all("loc" not in s["args"] for s in m["steps"])  # never coordinates


def test_binding_a_new_goal_and_filling_the_step():
    m = macro.compile_macro("play lofi on YouTube in Firefox", lofi_run(), STABLE)
    values = macro.bind(m["template"], "Play jazz piano on youtube in firefox.")
    assert values == {"p1": "jazz piano"}
    assert macro.fill(m["steps"][2]["args"], values)["text"] == "youtube.com/results?search_query=jazz+piano"
    assert macro.bind(m["template"], "open notepad") is None


def test_generated_content_and_unfindable_clicks_are_not_compiled():
    haiku = [step(0, "win_app", {"mode": "launch", "name": "notepad"}),
             step(1, "win_type", {"text": "Soft rain on the roof\nquiet puddles hold the sky\nthe street hums again"})]
    assert macro.compile_macro("write a haiku about rain in notepad", haiku, "Write a haiku in Notepad") is None
    blind = [step(0, "win_click", {"loc": [5, 5]})]  # no element recorded
    assert macro.compile_macro("click it", blind) is None
    assert macro.compile_macro("x", [step(0, "win_snapshot", {})]) is None


def test_structure_ignores_values_and_observation():
    assert macro.structure(lofi_run("lofi")) == macro.structure(lofi_run("jazz piano"))
    different = lofi_run()[:3]
    assert macro.structure(different) != macro.structure(lofi_run())
    assert macro.structure([step(0, "win_click", {}, ("button", "Go"), ok=False)]) == ""


def test_three_matching_runs_compile_the_skill_and_it_round_trips(tmp_path):
    store = KnowledgeStore(tmp_path / "k", fake_embed)
    skill, _ = store.upsert_skill({"title": "Play a YouTube video in Firefox", "apps": ["firefox"],
                                   "intent": "Play a video on YouTube matching a search query",
                                   "steps": ["Open Firefox", "Search YouTube", "Open the first result"]}, "approved")
    assert not maybe_compile(store, skill["id"], "play lofi on YouTube in Firefox", lofi_run("lofi"))
    assert not maybe_compile(store, skill["id"], "play jazz on YouTube in Firefox", lofi_run("jazz"))
    assert maybe_compile(store, skill["id"], "play rain sounds on YouTube in Firefox", lofi_run("rain sounds"))
    doc = store.get(skill["id"])
    assert doc["macro"] == "compiled" and doc["macro_def"]["template"] == "play {p1} on YouTube in Firefox"
    assert doc["steps"] == ["Open Firefox", "Search YouTube", "Open the first result"]  # the body survives
    store.record_outcome([skill["id"]], True, 5.0)
    assert store.get(skill["id"])["macro_def"] == doc["macro_def"]  # other writes keep the macro


def test_a_different_run_resets_the_count(tmp_path):
    store = KnowledgeStore(tmp_path / "k", fake_embed)
    skill, _ = store.upsert_skill({"title": "Play a YouTube video in Firefox", "steps": ["x"]}, "approved")
    maybe_compile(store, skill["id"], "play lofi on YouTube in Firefox", lofi_run())
    maybe_compile(store, skill["id"], "play lofi on YouTube in Firefox", lofi_run()[:3])  # went differently
    assert not maybe_compile(store, skill["id"], "play lofi on YouTube in Firefox", lofi_run())
    assert store.get(skill["id"])["macro"] == "none"
