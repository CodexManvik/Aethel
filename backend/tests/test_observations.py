from aethel.context.observations import StateTracker


def test_state_tracker_notes_only_identical_repeats_per_kind():
    t = StateTracker()
    assert t.seen("screen", "A", 1) is None
    assert t.seen("screen", "A", 3) == "[unchanged since step 1: same screen as then]"
    assert t.seen("page", "A", 4) is None          # kinds are independent
    assert t.seen("screen", "B", 5) is None        # changed: remembered as the new latest
    assert t.seen("screen", "B", 6) == "[unchanged since step 5: same screen as then]"
