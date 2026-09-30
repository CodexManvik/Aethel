"""Keeping tool results in the model's context only while they're worth their tokens
(token-efficiency spec §4.2, §5.1).

State observations ("screen", "page") describe how something looks now; a newer one of the same kind
supersedes the older. Content observations (a file, a web page, search results) are never stale."""
from dataclasses import dataclass

from ..providers.base import ChatMessage
from .builder import estimate

MIN_WORTH_MASKING = 500  # characters: shorter results cost less than the stub that would replace them


@dataclass
class Observation:
    index: int               # its tool message's position in the conversation
    tool: str
    kind: str | None         # "screen" | "page" for state; None for content
    step_no: int
    summary: str             # e.g. "fs_read of C:\\…\\question.docx", for the stub
    full: bool = True        # False when it was sent as an "unchanged since" note
    masked: bool = False


def _superseded(obs: list[Observation]) -> list[Observation]:
    """Full state observations with a later full one of the same kind (notes point back at full ones,
    so only a newer full snapshot makes an old one stale)."""
    latest: dict[str, int] = {}
    for o in obs:
        if o.kind and o.full:
            latest[o.kind] = o.index
    return [o for o in obs if o.kind and o.full and not o.masked and o.index < latest[o.kind]]


def mask_superseded(convo: list[ChatMessage], obs: list[Observation], batch: int, force: bool = False) -> int:
    """Replace stale screen/page snapshots with a one-line stub, in batches: rewriting earlier messages
    changes the prompt prefix the provider may have cached, so it's done at most once per `batch`."""
    stale = _superseded(obs)
    if not stale or (len(stale) < batch and not force):
        return 0
    for o in stale:
        convo[o.index].content = f"[earlier {o.kind} snapshot (step {o.step_no}) omitted: a newer one is below]"
        o.masked = True
    return len(stale)


def conversation_tokens(convo: list[ChatMessage]) -> int:
    return sum(estimate(m.content or "") + sum(estimate(c.arguments) for c in m.tool_calls or []) for m in convo)


def fit_to_budget(convo: list[ChatMessage], obs: list[Observation], budget: int, extra: int = 0) -> list[str]:
    """A last resort before the model's context overflows: stale state first, then content, oldest first,
    never the newest content observation. `extra` is what else the call sends (tool schemas).
    Returns what was left out."""
    dropped: list[str] = []
    if conversation_tokens(convo) + extra <= budget:
        return dropped
    mask_superseded(convo, obs, batch=1, force=True)
    content = [o for o in obs if o.kind is None and not o.masked]
    for o in content[:-1]:
        if conversation_tokens(convo) + extra <= budget:
            break
        if len(convo[o.index].content or "") < MIN_WORTH_MASKING:
            continue
        convo[o.index].content = f"[{o.summary} (step {o.step_no}) omitted to fit; call it again if you need it]"
        o.masked = True
        dropped.append(o.summary)
    return dropped


class StateTracker:
    """The latest content of each kind of state, so an identical one isn't sent twice."""

    def __init__(self) -> None:
        self._last: dict[str, tuple[str, int]] = {}

    def seen(self, kind: str, content: str, step_no: int) -> str | None:
        """A short note to send instead when `content` is the same as the last one of this kind, else None."""
        last = self._last.get(kind)
        if last is not None and last[0] == content:
            return f"[unchanged since step {last[1]}: same {kind} as then]"
        self._last[kind] = (content, step_no)
        return None
