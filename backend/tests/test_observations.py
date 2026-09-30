from aethel.context.observations import StateTracker


def test_state_tracker_notes_only_identical_repeats_per_kind():
    t = StateTracker()
    assert t.seen("screen", "A", 1) is None
    assert t.seen("screen", "A", 3) == "[unchanged since step 1: same screen as then]"
    assert t.seen("page", "A", 4) is None          # kinds are independent
    assert t.seen("screen", "B", 5) is None        # changed: remembered as the new latest
    assert t.seen("screen", "B", 6) == "[unchanged since step 5: same screen as then]"


from aethel.context.observations import Observation, fit_to_budget, mask_superseded  # noqa: E402
from aethel.providers.base import ChatMessage  # noqa: E402


def convo_with(*items):
    """items: (kind, content) -> a conversation of tool messages plus matching observations."""
    convo = [ChatMessage("system", "s")]
    obs = []
    for i, (kind, content) in enumerate(items, 1):
        convo.append(ChatMessage("tool", content, tool_call_id=f"c{i}"))
        obs.append(Observation(len(convo) - 1, "t", kind, i, f"t of thing{i}",
                               full=not content.startswith("[unchanged")))
    return convo, obs


def test_superseded_screens_are_masked_in_batches():
    convo, obs = convo_with(("screen", "S1"), ("screen", "S2"), ("screen", "S3"))
    assert mask_superseded(convo, obs, batch=3) == 0          # only 2 stale: wait for the batch
    convo.append(ChatMessage("tool", "S4", tool_call_id="c4"))
    obs.append(Observation(4, "t", "screen", 4, "t"))
    assert mask_superseded(convo, obs, batch=3) == 3
    assert [m.content for m in convo[1:]] == ["[earlier screen snapshot (step 1) omitted: a newer one is below]",
                                              "[earlier screen snapshot (step 2) omitted: a newer one is below]",
                                              "[earlier screen snapshot (step 3) omitted: a newer one is below]", "S4"]
    assert mask_superseded(convo, obs, batch=1) == 0          # never twice


def test_content_and_notes_and_other_kinds():
    convo, obs = convo_with(("screen", "S1"), (None, "file text"), ("screen", "[unchanged since step 1: same screen]"),
                            ("page", "P1"))
    assert mask_superseded(convo, obs, batch=1, force=True) == 0  # the note doesn't supersede S1; P1 is another kind
    convo2, obs2 = convo_with(("screen", "S1"), (None, "file text"), ("screen", "S2"))
    assert mask_superseded(convo2, obs2, batch=1) == 1 and convo2[2].content == "file text"


def test_fit_to_budget_drops_stale_state_then_oldest_content_never_the_newest():
    big = "x" * 4000  # ~1000 tokens each
    convo, obs = convo_with(("screen", big), (None, big), (None, big), ("screen", big), (None, big))
    dropped = fit_to_budget(convo, obs, budget=2500)  # 5 x ~1000: stale screen, then 2 of the 3 contents go
    assert dropped == ["t of thing2", "t of thing3"]
    assert convo[1].content.startswith("[earlier screen snapshot")
    assert convo[2].content == "[t of thing2 (step 2) omitted to fit; call it again if you need it]"
    assert convo[5].content == big and convo[4].content == big  # the newest content and the latest screen stay


def test_fit_to_budget_does_nothing_when_it_fits():
    convo, obs = convo_with(("screen", "S1"), ("screen", "S2"))
    assert fit_to_budget(convo, obs, budget=10_000) == [] and convo[1].content == "S1"
