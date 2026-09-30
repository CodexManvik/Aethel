from aethel.context.builder import NEVER_DROP, Section, budget_for, build, estimate
from aethel.providers.base import ChatMessage
from aethel.settings import AppSettings


def window(n, size=40):
    return [ChatMessage("user" if i % 2 == 0 else "assistant", f"m{i} " + "x" * size) for i in range(n)]


def sections():
    return [Section("persona", NEVER_DROP, "PERSONA " + "p" * 40),
            Section("facts", 20, "FACTS " + "f" * 400, ["f1"]),
            Section("episodes", 10, "EPISODES " + "e" * 400, ["e1"])]


def test_everything_fits_keeps_order():
    built = build(sections(), window(3), window_min=2, budget=10_000)
    system = built.messages[0]
    assert system.role == "system"
    assert system.content.index("PERSONA") < system.content.index("FACTS") < system.content.index("EPISODES")
    assert [m.content for m in built.messages[1:]] == [m.content for m in window(3)]
    assert built.included == {"facts": ["f1"], "episodes": ["e1"]} and built.dropped == []
    assert built.est_tokens == estimate(system.content) + sum(estimate(m.content) for m in window(3))


def test_drops_lowest_priority_first_then_trims_window():
    full = build(sections(), window(6), window_min=2, budget=10_000).est_tokens
    no_episodes = build(sections(), window(6), window_min=2, budget=full - 50)
    assert no_episodes.dropped == ["episodes"] and len(no_episodes.messages) == 7
    assert "EPISODES" not in no_episodes.messages[0].content
    tight = build(sections(), window(6), window_min=2, budget=estimate("PERSONA " + "p" * 40) + 40)
    assert tight.dropped == ["episodes", "facts"]
    assert len(tight.messages) == 1 + 2  # trimmed to window_min, oldest first
    assert tight.messages[-1].content == window(6)[-1].content
    assert tight.included == {}


def test_never_drops_persona_even_if_over_budget():
    built = build(sections(), window(5), window_min=3, budget=10)
    assert "PERSONA" in built.messages[0].content and len(built.messages) == 1 + 3
    assert built.est_tokens > 10


def test_empty_sections_are_skipped():
    built = build([Section("persona", NEVER_DROP, "P"), Section("facts", 20, ""), Section("episodes", 10, "E")],
                  [], window_min=0, budget=1000)
    assert built.messages[0].content == "P\n\nE" and built.included == {}


def test_budget_uses_smallest_in_chain_and_cap():
    s = AppSettings.model_validate({
        "roles": {"chat": [{"provider": "groq", "model": "g", "context_size": 131072},
                           {"provider": "local", "model": "local"}]},
        "local_llm": {"context_size": 4096}})
    assert budget_for(s, "chat", 1024) == int(4096 * 0.9) - 1024
    s2 = s.model_copy(update={"local_llm": s.local_llm.model_copy(update={"context_size": 0})})
    # a local model run with context_size 0 is assumed small (8192), which is below the cap
    assert budget_for(s2, "chat", 1024) == int(8192 * 0.9) - 1024
    s3 = AppSettings.model_validate({"roles": {"chat": [{"provider": "groq", "model": "g", "context_size": 131072}]}})
    assert budget_for(s3, "chat", 1024) == int(16000 * 0.9) - 1024
    assert budget_for(s3, "nope", 100_000) == 256  # never below the floor
