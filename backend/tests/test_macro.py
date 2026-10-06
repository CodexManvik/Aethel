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


def test_repair_rebuilds_after_a_rescued_run_and_breaks_after_two_failures(tmp_path):
    from aethel.runtime.reflect import repair
    store = KnowledgeStore(tmp_path / "k", fake_embed)
    skill, _ = store.upsert_skill({"title": "Play a YouTube video in Firefox", "apps": ["firefox"],
                                   "intent": "Play a video on YouTube matching a search query", "steps": ["x"]},
                                  "approved")
    goal = "play lofi on YouTube in Firefox"
    store.set_macro(skill["id"], macro.compile_macro(goal, lofi_run(), STABLE), "compiled")
    clean = [s.model_copy(update={"decider": "macro"}) for s in lofi_run()]
    rescued = [s.model_copy(update={"decider": "macro"}) for s in lofi_run()[:4]] + \
              [s.model_copy(update={"decider": "agent"}) for s in lofi_run()[4:]]
    assert repair(store, skill["id"], goal, clean, True) == "clean"
    assert repair(store, skill["id"], goal, rescued, True) == "repaired"
    assert repair(store, skill["id"], goal, rescued, False) == "repair failed"
    assert repair(store, skill["id"], goal, rescued, False) == "broken"
    assert store.get(skill["id"])["macro"] == "broken"


# ---- browser steps (Phase 3 spec §7.6) ----------------------------------------------------------------------------
BSTABLE = "Search Bing for something. Search the web with Bing for a query. browser"


def bstep(i, tool, args, element=None, ok=True, host="www.bing.com"):
    meta = {"app": "browser", **({"element": {"role": element[0], "name": element[1], "window": host}} if element else {})}
    return StepRecord(id=f"b{i}", task_id="t", idx=i, tool=tool, args=args, summary="", verdict="allow", ok=ok,
                      result="", duration_ms=10, created_at="", meta=meta)


def bing_run(query="leeds library hours"):
    return [
        bstep(0, "browser_navigate", {"url": "https://www.bing.com/"}),
        bstep(1, "browser_snapshot", {}),
        bstep(2, "browser_type", {"target": "e5", "element": "Search box", "text": query, "submit": True},
              ("textbox", "Search")),
        bstep(3, "browser_wait_for", {"time": 2}),
        bstep(4, "browser_click", {"target": "e21", "element": "first result"}, ("link", "Opening hours")),
        bstep(5, "browser_tabs", {"action": "list"}),
    ]


def test_a_browser_run_compiles_by_role_name_and_host_never_by_ref():
    m = macro.compile_macro("search Bing for leeds library hours", bing_run(), BSTABLE)
    assert m["template"] == "search Bing for {p1}" and m["params"] == ["p1"]
    assert [s["tool"] for s in m["steps"]] == ["browser_navigate", "browser_type", "browser_click"]   # looking is dropped
    typed = m["steps"][1]
    assert typed["args"] == {"text": "{p1}", "submit": True}                    # a ref belongs to one snapshot: never kept
    assert typed["target"] == {"role": "textbox", "name": "Search", "window": "www.bing.com"}
    assert m["steps"][2]["args"] == {} and m["steps"][2]["target"]["window"] == "www.bing.com"
    assert all("target" not in s["args"] and "element" not in s["args"] for s in m["steps"])


def test_a_url_with_the_query_in_it_becomes_a_parameter_too():
    run = [bstep(0, "browser_navigate", {"url": "https://www.bing.com/search?q=leeds+library+hours"}),
           bstep(1, "browser_click", {"target": "e9"}, ("link", "Opening hours"))]
    m = macro.compile_macro("search Bing for leeds library hours", run, BSTABLE)
    assert m["steps"][0]["args"] == {"url": "https://www.bing.com/search?q={p1+}"}
    values = macro.bind(m["template"], "search bing for jazz piano lessons")
    assert macro.fill(m["steps"][0]["args"], values)["url"] == "https://www.bing.com/search?q=jazz+piano+lessons"
    # a long constant address is normal (only typed text is judged to be generated content)
    long_url = "https://www.example.org/" + "a" * 120
    assert macro.compile_macro("open it", [bstep(0, "browser_navigate", {"url": long_url})]) is not None


def test_a_form_fill_a_blind_action_or_generated_text_makes_a_browser_run_uncompilable():
    form = [bstep(0, "browser_navigate", {"url": "https://shop.example/"}),
            bstep(1, "browser_fill_form", {"fields": [{"target": "e3", "name": "Name", "type": "textbox", "value": "Ada"}]})]
    assert macro.compile_macro("fill in the form", form) is None            # form values aren't a navigation pattern
    blind = [bstep(0, "browser_click", {"target": "e4"})]                     # nothing recorded about the element
    assert macro.compile_macro("click it", blind) is None
    typed_blind = [bstep(0, "browser_type", {"target": "e5", "text": "x"})]
    assert macro.compile_macro("type x", typed_blind) is None                 # a browser_type always needs its target
    essay = [bstep(0, "browser_type", {"target": "e5", "text": "A long generated paragraph that goes on and on."},
                   ("textbox", "Comment"))]
    assert macro.compile_macro("write a comment", essay) is None
    assert macro.compile_macro("x", [bstep(0, "browser_snapshot", {}), bstep(1, "browser_tabs", {"action": "list"})]) is None


def test_keys_dialogs_and_going_back_replay_without_a_target():
    run = [bstep(0, "browser_navigate", {"url": "https://a.example/"}), bstep(1, "browser_press_key", {"key": "Escape"}),
           bstep(2, "browser_handle_dialog", {"accept": True}), bstep(3, "browser_navigate_back", {})]
    m = macro.compile_macro("do the dance", run)
    assert [s["tool"] for s in m["steps"]] == ["browser_navigate", "browser_press_key", "browser_handle_dialog",
                                               "browser_navigate_back"]
    assert m["steps"][1]["args"] == {"key": "Escape"} and "target" not in m["steps"][1]


def test_browser_and_desktop_steps_mix_in_one_macro():
    run = [step(0, "win_app", {"mode": "launch", "name": "firefox"}), *bing_run()[:3]]
    m = macro.compile_macro("search Bing for leeds library hours", run, BSTABLE)
    assert [s["tool"] for s in m["steps"]] == ["win_app", "browser_navigate", "browser_type"]


def test_the_structure_of_a_browser_run_ignores_values_refs_and_looking():
    assert macro.structure(bing_run("leeds library hours")) == macro.structure(bing_run("jazz piano lessons"))
    refs = bing_run()
    refs[2] = bstep(2, "browser_type", {"target": "e99", "text": "x"}, ("textbox", "Search"))
    assert macro.structure(refs) == macro.structure(bing_run())              # a different ref is the same step
    assert macro.structure(bing_run()[:3]) != macro.structure(bing_run())
    assert macro.structure([bstep(0, "browser_click", {}, ("button", "Go"), ok=False)]) == ""


def test_a_browser_macro_reads_as_a_plan():
    m = macro.compile_macro("search Bing for leeds library hours", bing_run(), BSTABLE)
    values = {"p1": "jazz piano"}
    assert [macro.describe(s, values) for s in m["steps"]] == [
        "Go to https://www.bing.com/", "Type “jazz piano” into “Search”", "Click “Opening hours”"]
    assert macro.describe({"tool": "browser_navigate_back", "args": {}}, {}) == "Go back"
    assert macro.describe({"tool": "browser_press_key", "args": {"key": "Escape"}}, {}) == "Press Escape"
    assert macro.describe({"tool": "browser_select_option", "args": {"values": ["Green"]},
                           "target": {"role": "combobox", "name": "Colour", "window": "x"}}, {}) == "Choose Green in “Colour”"
    assert macro.describe({"tool": "browser_hover", "args": {}, "target": {"role": "link", "name": "Menu", "window": "x"}}, {}) \
        == "Hover over “Menu”"
    assert macro.describe({"tool": "browser_handle_dialog", "args": {"accept": True}}, {}) == "Accept the dialog"
