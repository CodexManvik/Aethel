from aethel.context.observations import (UNCHANGED_PREFIX, Observation, StateTracker, fit_to_budget,
                                         mask_superseded)
from aethel.providers.base import ChatMessage

STALE = "[an earlier screen snapshot, left out: a newer one is below]"


def test_state_tracker_notes_only_identical_repeats_per_kind():
    t = StateTracker()
    assert t.seen("screen", "A") is None
    assert t.seen("screen", "A").startswith(UNCHANGED_PREFIX)
    assert t.seen("page", "A") is None          # kinds are independent
    assert t.seen("screen", "B") is None        # changed: remembered as the new latest
    assert t.seen("screen", "B") == "[unchanged: the screen is exactly as in the last screen snapshot above]"


def convo_with(*items):
    """items: (kind, content) -> a conversation of tool messages plus matching observations."""
    convo = [ChatMessage("system", "s")]
    obs = []
    for i, (kind, content) in enumerate(items, 1):
        convo.append(ChatMessage("tool", content, tool_call_id=f"c{i}"))
        obs.append(Observation(len(convo) - 1, "t", kind, f"t of thing{i}", signature=f"sig{i}",
                               full=not content.startswith(UNCHANGED_PREFIX)))
    return convo, obs


def test_superseded_screens_are_masked_in_batches():
    convo, obs = convo_with(("screen", "S1"), ("screen", "S2"), ("screen", "S3"))
    assert mask_superseded(convo, obs, batch=3) == 0          # only 2 stale: wait for the batch
    convo.append(ChatMessage("tool", "S4", tool_call_id="c4"))
    obs.append(Observation(4, "t", "screen", "t"))
    assert mask_superseded(convo, obs, batch=3) == 3
    assert [m.content for m in convo[1:]] == [STALE, STALE, STALE, "S4"]
    assert mask_superseded(convo, obs, batch=1) == 0          # never twice
    assert [m.tool_call_id for m in convo[1:]] == ["c1", "c2", "c3", "c4"]  # pairing untouched


def test_content_and_notes_and_other_kinds():
    convo, obs = convo_with(("screen", "S1"), (None, "file text"),
                            ("screen", f"{UNCHANGED_PREFIX} the screen is exactly as in the last screen snapshot above]"),
                            ("page", "P1"))
    assert mask_superseded(convo, obs, batch=1, force=True) == 0  # a note doesn't supersede S1; P1 is another kind
    convo2, obs2 = convo_with(("screen", "S1"), (None, "file text"), ("screen", "S2"))
    assert mask_superseded(convo2, obs2, batch=1) == 1 and convo2[2].content == "file text"


def test_fit_to_budget_drops_stale_state_then_oldest_content_never_the_newest():
    big = "x" * 4000  # ~1000 tokens each
    convo, obs = convo_with(("screen", big), (None, big), (None, big), ("screen", big), (None, big))
    dropped = fit_to_budget(convo, obs, budget=2500)  # stale screen, then 2 of the 3 contents go
    assert [o.summary for o in dropped] == ["t of thing2", "t of thing3"]
    assert [o.signature for o in dropped] == ["sig2", "sig3"]
    assert convo[1].content == STALE
    assert convo[2].content == "[t of thing2: left out to fit; call it again if you need it]"
    assert convo[5].content == big and convo[4].content == big  # the newest content and the latest screen stay


def test_fit_to_budget_does_nothing_when_it_fits():
    convo, obs = convo_with(("screen", "S1"), ("screen", "S2"))
    assert fit_to_budget(convo, obs, budget=10_000) == [] and convo[1].content == "S1"


def test_the_guard_has_room_with_default_settings():
    """Regression: the guard once took the smallest model in the chain (local, 8k) minus an 8k reply,
    leaving 256 tokens, and masked history on every call."""
    from aethel.context.builder import budget_for
    from aethel.settings import AppSettings, RouteEntry
    s = AppSettings()
    gemini = [RouteEntry(provider="gemini", model="gemini-2.5-flash")]
    assert budget_for(s, "agent", s.agent_max_tokens, capped=False, entries=gemini) == int(32768 * 0.9) - 8192
    local = [RouteEntry(provider="local", model="local")]
    assert budget_for(s, "agent", s.agent_max_tokens, capped=False, entries=local) == int(8192 * 0.9) - 2048
    assert budget_for(s, "agent", s.agent_max_tokens) > 4000  # the chain-wide, capped figure isn't starved either
