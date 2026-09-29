"""Macros: a skill that keeps succeeding the same way becomes a replayable
sequence of tool calls (spec §6.3, tier 3). Compilation is deterministic code,
not an LLM call.

A macro step addresses elements by {role, name, window}, never by screen
coordinates: those are found again on the live screen at replay time
(grounding). Typed text that came from the goal becomes a parameter, bound
from the next goal through the goal's template. A run that typed generated
content (a poem, an essay) isn't compiled: macros are for navigation."""
import hashlib
import re

# Steps that only look: they're dropped from structures and macros (grounding snapshots itself).
OBSERVE = {"win_snapshot", "win_wait_for", "win_displays", "win_locate"}
# Steps that act on an element at a screen position, which must be found again at replay.
POINTER = {"win_click", "win_move", "win_scroll", "win_multi_select"}
MAX_CONSTANT_TEXT = 40  # longer (or multi-line) typed text is treated as generated content
REPEATS_TO_COMPILE = 3


def _replayable(step) -> bool:
    return bool(step.ok) and step.tool.startswith("win_") and step.tool not in OBSERVE


def _target(step) -> dict | None:
    el = (step.meta or {}).get("element")
    return {"role": el["role"], "name": el["name"], "window": el.get("window", "")} if el else None


def structure(steps) -> str:
    """A short key for how a run went: its acting steps and the elements they touched."""
    parts = [f"{s.tool}|{(_target(s) or {}).get('role', '')}|{(_target(s) or {}).get('name', '')}"
             for s in steps if _replayable(s)]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16] if parts else ""


STOPWORDS = {"a", "an", "the", "on", "in", "to", "of", "for", "and", "or", "with", "my", "me", "it", "at", "by",
              "from", "into", "please", "some", "this", "that"}


def _variable_spans(goal: str, stable: str, typed: list[str]) -> list[str]:
    """Runs of goal words that aren't part of the skill itself (its title, intent,
    apps) and that were typed somewhere: those are what changes between tasks."""
    stable_words = set(re.findall(r"[a-z0-9]+", stable.lower()))
    typed_l = [t.lower() for t in typed]
    spans, current = [], []
    for word in re.findall(r"\S+", goal):
        w = re.sub(r"[^\w]", "", word.lower())
        variable = w and w not in stable_words and w not in STOPWORDS and any(w in t for t in typed_l)
        if variable:
            current.append(word.strip(".,!?\"'"))
        elif current:
            spans.append(" ".join(current))
            current = []
    if current:
        spans.append(" ".join(current))
    return [s for s in spans if any(s.lower() in t or s.lower().replace(" ", "+") in t for t in typed_l)]


def compile_macro(goal: str, steps, stable: str = "") -> dict | None:
    """{template, params, steps} from a successful run, or None if it can't be replayed.
    `stable` is the skill's own wording (title, intent, apps): words in it are never parameters."""
    replay = [s for s in steps if _replayable(s)]
    typed = [str(s.args.get("text") or "") for s in replay if s.tool == "win_type"]
    spans = _variable_spans(goal, stable, typed)
    names = {span.lower(): f"p{i}" for i, span in enumerate(spans, 1)}
    out = []
    for s in replay:
        args = {k: v for k, v in s.args.items() if k not in ("loc", "locs", "label", "labels")}
        target = _target(s)
        needs_target = s.tool in POINTER or (s.tool == "win_type" and s.args.get("loc") is not None)
        if needs_target and target is None:
            return None  # a click we can't find again by name
        if s.tool == "win_type":
            text = str(args.get("text") or "")
            url_like = " " not in text and bool(re.search(r"[/?=]", text))  # spaces go in as '+'
            for span, name in names.items():
                form = span.replace(" ", "+") if url_like else span
                text = re.sub(re.escape(form), "{" + name + ("+}" if url_like else "}"), text, flags=re.IGNORECASE)
            if len(re.sub(r"\{p\d+\+?\}", "", text)) > MAX_CONSTANT_TEXT or "\n" in text:
                return None  # generated content, different every time
            args["text"] = text
        out.append({"tool": s.tool, "args": args, **({"target": target} if target else {})})
    if not out:
        return None
    template = goal
    for span, name in names.items():
        template = re.sub(re.escape(span), "{" + name + "}", template, count=1, flags=re.IGNORECASE)
    return {"template": template, "params": sorted(names.values()), "steps": out}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().rstrip(".!?")).lower()


def bind(template: str, goal: str) -> dict[str, str] | None:
    """Parameter values for a new goal, or None if it doesn't fit the template."""
    pattern, names = "", []
    for piece in re.split(r"(\{p\d+\})", _norm(template)):
        m = re.fullmatch(r"\{(p\d+)\}", piece)
        if m:
            names.append(m.group(1))
            pattern += f"(?P<{m.group(1)}>.+?)"
        else:
            pattern += re.escape(piece)
    match = re.fullmatch(pattern, _norm(goal))
    if match is None:
        return None
    return {n: match.group(n).strip() for n in names}


def fill(args: dict, values: dict[str, str]) -> dict:
    """Macro step args with {pN} replaced by this run's values ({pN+}: spaces as '+', as in a URL)."""
    def one(m: re.Match) -> str:
        value = values.get(m.group(1))
        if value is None:
            return m.group(0)
        return value.replace(" ", "+") if m.group(2) else value
    return {k: (re.sub(r"\{(p\d+)(\+)?\}", one, v) if isinstance(v, str) else v) for k, v in args.items()}
