import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "eval_s1_intent", Path(__file__).resolve().parents[2] / "scripts" / "eval_s1_intent.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_threshold_metrics_and_pick():
    probs = [0.95, 0.8, 0.6, 0.55, 0.2, 0.1]
    labels = [True, True, False, True, False, False]
    m = ev.at(probs, labels, 0.7)
    assert (m["precision"], m["fired"]) == (1.0, 2) and abs(m["recall"] - 2 / 3) < 1e-9
    assert ev.pick(probs, labels) == 0.7  # 0.5 and 0.6 let the false 0.6 through
    assert ev.at(probs, labels, 0.99)["precision"] is None


def test_ece_is_zero_when_perfectly_calibrated_and_large_when_not():
    assert ev.ece([1.0, 1.0, 0.0], [True, True, False]) == 0.0
    assert ev.ece([0.95] * 4, [False] * 4) > 0.9


def test_skill_selection_metrics():
    spec2 = importlib.util.spec_from_file_location(
        "eval_s1_skill", Path(__file__).resolve().parents[2] / "scripts" / "eval_s1_skill.py")
    sk = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(sk)
    rows = [{"label": "a"}, {"label": "b"}, {"label": None}, {"label": "c"}]
    picks = [("a", 0.9), ("x", 0.4), ("none", 0.99), ("c", 0.35)]
    m = sk.at(rows, picks, 0.3)
    assert (m["picked"], m["wrong_picks"], m["precision"]) == (3, 1, 2 / 3)
    assert sk.at(rows, picks, 0.5) == {"threshold": 0.5, "precision": 1.0, "coverage": 1 / 3, "picked": 1,
                                       "wrong_picks": 0}
    assert sk.choose([rows[0], rows[3]], [picks[0], picks[3]]) == 0.3


def _load(name):
    s = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def test_fact_gate_picks_the_highest_threshold_that_keeps_recall():
    fact = _load("eval_s1_fact")
    probs = [0.9, 0.6, 0.35, 0.12, 0.5, 0.08, 0.02]
    labels = [True, True, True, True, False, False, False]
    assert fact.pick(probs, labels) == 0.1   # 0.15 and up miss the 0.12 fact: recall 0.75 < 0.9
    assert fact.pick([0.01, 0.9], [True, False]) is None  # no threshold on the grid catches the fact
    assert fact.split([0, 1, 2, 3, 4]) == ([0, 2, 4], [1, 3])


def test_episodic_floor_scores_like_chat_recall():
    ep = _load("eval_episodic")
    rows = [
        {"expect": ["a:0"], "hits": [["a:1", 0.80], ["a:0", 0.70]]},  # right one second: correct at 0.7
        {"expect": ["b:0"], "hits": [["c:0", 0.75], ["b:0", 0.60]]},  # right one below 0.7: wrong
        {"expect": [], "hits": [["a:0", 0.66]]},                      # nothing relevant exists
    ]
    assert ep.at(rows, 0.70) == {"threshold": 0.70, "correct": 1, "wrong": 1, "unwanted": 0, "recall": 0.5,
                                 "precision": 0.5}
    assert ep.at(rows, 0.60)["unwanted"] == 1 and ep.at(rows, 0.60)["correct"] == 2
    assert ep.at(rows, 0.90)["precision"] is None
    # net correct - wrong - unwanted: 1 up to 0.60 (b:0 still recalled), -1 at 0.61-0.66, 0 at 0.67-0.70 …
    assert ep.choose_floor(rows) == 0.6  # … and ties go to the higher floor


def test_extraction_scoring():
    ex = _load("eval_extract")
    case = {"expect": [{"op": "update", "id": "f1", "contains": ["York"]}, {"op": "add", "contains": ["dog", "pip"]}]}
    assert ex.score(case, [{"op": "add", "scope": "user", "text": "Has a dog called Pip"},
                           {"op": "update", "id": "f1", "text": "Lives in York"}])
    assert not ex.score(case, [{"op": "update", "id": "f2", "text": "Lives in York"},
                               {"op": "add", "text": "Has a dog called Pip"}])        # wrong id
    assert not ex.score(case, [{"op": "update", "id": "f1", "text": "Lives in York"}])  # one missing
    assert ex.score({"expect": []}, []) and not ex.score({"expect": []}, [{"op": "add", "text": "x"}])
    assert ex.score({"expect": []}, None)


def test_extraction_fixtures_are_well_formed():
    import json
    cases = json.loads((Path(__file__).resolve().parents[1] / "aethel" / "eval" / "extract_fixtures.json")
                       .read_text(encoding="utf-8"))["cases"]
    assert len(cases) == 20 and sum(1 for c in cases if c["expect"]) == 12
    for c in cases:
        for e in c["expect"]:
            assert e["op"] in ("add", "update", "delete")
            if e["op"] != "add":
                assert e["id"].startswith("f") and int(e["id"][1:]) <= len(c["known"])


def test_token_eval_summary():
    tok = _load("eval_tokens")
    runs = [{"success": True, "seconds": 10, "usage": {"prompt": 1000, "completion": 100, "calls": 4, "estimated": False}},
            {"success": False, "seconds": 20, "usage": {"prompt": 3000, "completion": 300, "calls": 8, "estimated": True}}]
    s = tok.summarise(runs)
    assert (s["success_rate"], s["mean_prompt_tokens"], s["mean_calls"], s["estimated"]) == (0.5, 2000, 6, True)
    assert [t[0] for t in tok.tasks()] == ["haiku", "calc", "folder", "url", "edit", "display"]


def test_token_eval_interleaves_arms_and_checks_results(tmp_path, monkeypatch):
    tok = _load("eval_tokens")
    assert tok.schedule(["a", "b"], 2, "masking") == [("a", "masking off"), ("a", "masking on"), ("b", "masking off"),
                                           ("b", "masking on"), ("a", "masking on"), ("a", "masking off"),
                                           ("b", "masking on"), ("b", "masking off")]
    checks = {name: check for name, _, check in tok.tasks()}
    (tmp_path / "result.txt").write_text("7,006,652", encoding="utf-8")
    assert checks["calc"](tmp_path) is True
    (tmp_path / "notes.txt").write_text("shopping\n- eggs\nbuy milk\n", encoding="utf-8")
    assert checks["edit"](tmp_path) is True
    assert checks["folder"](tmp_path) is False and checks["url"] is None


def test_token_eval_has_two_axes_and_pins_everything_else(tmp_path):
    tok = _load("eval_tokens")
    flip = [("a", "groups off"), ("a", "groups on"), ("b", "groups off"), ("b", "groups on"),
            ("a", "groups on"), ("a", "groups off"), ("b", "groups on"), ("b", "groups off")]
    assert tok.schedule(["a", "b"], 2, "groups") == flip
    assert tok.schedule(["a"], 1, "masking") == [("a", "masking off"), ("a", "masking on")]
    assert list(tok.arms_for("groups")) == ["groups off", "groups on"]
    # compaction is lossless, so it's the baseline of every arm; the axis under test is the only difference
    base = {"compact_schemas": True, "mask_superseded": True, "mask_batch": 3, "tool_groups": False}
    assert tok.arms_for("groups")["groups off"] == base
    assert tok.arms_for("groups")["groups on"] == {**base, "tool_groups": True}
    assert tok.arms_for("masking")["masking off"] == {**base, "mask_superseded": False}
    assert tok.arms_for("masking")["masking on"] == {**base, "mask_batch": 1}   # the strict version, as before


def test_token_eval_summary_reports_the_schema_tokens_and_the_verdict_on_the_arms():
    tok = _load("eval_tokens")
    usage = {"prompt": 1000, "completion": 50, "calls": 4, "estimated": False}
    runs = [{"success": True, "seconds": 10.0, "usage": usage, "tool_tokens": 9000.0},
            {"success": False, "seconds": 20.0, "usage": usage, "tool_tokens": 3000.0},
            {"success": True, "seconds": 12.0, "usage": usage, "tool_tokens": None}]  # no execute call recorded
    s = tok.summarise(runs)
    assert s["mean_tool_tokens"] == 6000.0 and s["success_rate"] == round(2 / 3, 3)
    assert tok.summarise([])["mean_tool_tokens"] is None
    off = {"summary": {"runs": 12, "success_rate": round(10 / 12, 3)}}
    assert tok.verdict(off, {"summary": {"runs": 12, "success_rate": round(9 / 12, 3)}}) == "no_drop"   # one run fewer
    assert tok.verdict(off, {"summary": {"runs": 12, "success_rate": round(8 / 12, 3)}}) == "drop"
    assert tok.verdict(off, {"summary": {"runs": 12, "success_rate": 1.0}}) == "no_drop"
    assert tok.verdict(off, {"summary": {"runs": 0, "success_rate": None}}) == "not_measured"


def test_token_eval_only_approves_the_task_own_windows():
    tok = _load("eval_tokens")
    work = str(tok.WORK)
    assert tok.decide("haiku", "win_type", "Type “Rain softly falls…” in notepad", "write") == "allow_once"
    assert tok.decide("haiku", "win_type", "Type “Pitter patter…” in whatsapp.root", "write") == "deny"
    assert tok.decide("haiku", "win_shortcut", "Press enter in whatsapp.root", "write") == "deny"
    assert tok.decide("haiku", "win_shortcut", "Press ctrl+s", "write") == "deny"   # no window named: no
    assert tok.decide("haiku", "fs_write", work + r"\haiku.txt", "write") == "allow_once"
    assert tok.decide("haiku", "fs_write", r"C:\Users\x\Desktop\a.txt", "write") == "deny"
    # Store apps (Calculator, Settings) report their host window rather than their own name.
    assert tok.decide("calc", "win_type", "Type “1234” in applicationframehost", "write") == "allow_once"
    assert tok.decide("display", "win_click", "Click “System” in applicationframehost", "write") == "allow_once"
    assert tok.decide("haiku", "win_type", "Type “x” in applicationframehost", "write") == "deny"
    # whole words only: "edge" must not match a window that merely contains it
    assert tok.decide("url", "win_click", "Click “x” in Microsoft Edge", "write") == "allow_once"
    assert tok.decide("url", "win_click", "Click “x” in Knowledge Base - Chrome", "write") == "deny"
    assert tok.decide("url", "win_click", "Click “Send” in edge", "irreversible") == "deny"
    assert tok.decide("display", "shell", "run something", "write") == "deny"


def test_schema_report_ranks_tools_by_their_share_of_the_tokens():
    rep = _load("tool_schema_report")
    from aethel.providers.base import ToolSpec
    specs = [ToolSpec("a", "x", {}), ToolSpec("b", "x", {}), ToolSpec("c", "x", {})]
    rows = rep.report(specs, [100, 300, 600], {"a": "core", "b": "office", "c": "files"})
    assert [r["tool"] for r in rows] == ["c", "b", "a"]
    assert [r["tokens"] for r in rows] == [600, 300, 100]
    assert [r["share"] for r in rows] == [0.6, 0.3, 0.1]
    assert [r["group"] for r in rows] == ["files", "office", "core"]
    assert rep.by_group(rows) == {"files": 600, "office": 300, "core": 100}


def test_schema_report_estimates_when_there_is_no_tokenizer():
    rep = _load("tool_schema_report")
    assert rep.count_tokens(["x" * 40, "y" * 7], None) == [10, 1]
    # nothing is listening there: it estimates instead of failing
    assert rep.count_tokens(["x" * 40], "http://127.0.0.1:9/v1") == [10]
    assert rep.exact_counts(["x" * 40], "http://127.0.0.1:9/v1") is None


def test_schema_report_measures_what_the_provider_receives():
    import json
    from aethel.providers.base import ToolSpec
    rep = _load("tool_schema_report")
    spec = ToolSpec("win_wait", "Wait.", {"type": "object", "properties": {"duration": {"type": "number"}}})
    wire = json.loads(rep.spec_json(spec))
    assert wire["function"]["name"] == "win_wait" and wire["function"]["parameters"]["properties"]["duration"]
