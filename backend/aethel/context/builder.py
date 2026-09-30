"""The context builder (Phase 3 spec §4): every model call's prompt is assembled here, within a token budget.

A prompt is a system message made of sections (persona, task note, remembered facts, earlier conversations,
learned skills…) followed by the conversation window. When it's over budget, whole sections go first,
lowest priority first; then the window loses its oldest messages, down to a minimum. The ids of what was
included are returned, so a reply can say what it recalled (and E5 can measure it)."""
from dataclasses import dataclass, field

from ..providers.base import ChatMessage
from ..settings import AppSettings

NEVER_DROP = 1_000_000
SAFETY = 0.9          # token estimates are rough: keep 10% spare
MIN_BUDGET = 256      # only a misconfigured role (reply longer than the context) falls back to this
DEFAULT_CONTEXT = 8192         # a role with no entries at all
LOCAL_UNKNOWN_CONTEXT = 8192   # a local model run with context_size 0


def estimate(text: str) -> int:
    """Roughly 4 characters per token for English, plus a little per-message overhead. There's no
    per-provider tokenizer; the safety margin in budget_for absorbs the error."""
    return len(text) // 4 + 4


@dataclass
class Section:
    key: str
    priority: int           # higher survives longer; NEVER_DROP is never dropped
    text: str
    ids: list[str] = field(default_factory=list)


@dataclass
class BuiltContext:
    messages: list[ChatMessage]
    included: dict[str, list[str]]
    dropped: list[str]
    est_tokens: int


def build(sections: list[Section], window: list[ChatMessage], *, window_min: int, budget: int) -> BuiltContext:
    kept = [s for s in sections if s.text]
    window = list(window)
    dropped: list[str] = []

    def total() -> int:
        return estimate("\n\n".join(s.text for s in kept)) + sum(estimate(m.content) for m in window)

    while total() > budget:
        droppable = [s for s in kept if s.priority < NEVER_DROP]
        if not droppable:
            break
        lowest = min(droppable, key=lambda s: s.priority)
        kept.remove(lowest)
        dropped.append(lowest.key)
    while total() > budget and len(window) > window_min:
        window.pop(0)
    system = "\n\n".join(s.text for s in kept)
    return BuiltContext(messages=[ChatMessage("system", system)] + window,
                        included={s.key: list(s.ids) for s in kept if s.ids},
                        dropped=dropped, est_tokens=total())


def _size(entry, settings: AppSettings) -> int:
    if entry.provider == "local":
        # llama-server gets -c context_size; 0 means the model's own size, which we can't know: assume a small one
        return settings.local_llm.context_size or LOCAL_UNKNOWN_CONTEXT
    return entry.context_size


def budget_for(settings: AppSettings, role: str, reply_tokens: int, capped: bool = True,
               entries: list | None = None) -> int:
    """Tokens available for the prompt, less the reply and a safety margin.
    - Default: the smallest context in the role's failover chain (a switch mid-reply must still fit),
      capped per role. For deciding what to include, where erring small only costs a little recall.
    - capped=False with `entries` = the model about to be called (RoleRouter.primary): its real limit,
      for guards that must act only when a call would otherwise fail.
    The reply's reservation is at most a quarter of the context: a model can't use more than it has, and
    reserving an 8k reply in an 8k model would leave nothing for the prompt."""
    chosen = entries if entries is not None else settings.roles.get(role, [])
    sizes = [_size(e, settings) for e in chosen] or [DEFAULT_CONTEXT]
    smallest = min(sizes)
    base = min(smallest, settings.context_caps.get(role, smallest)) if capped else smallest
    return max(MIN_BUDGET, int(base * SAFETY) - min(reply_tokens, base // 4))
