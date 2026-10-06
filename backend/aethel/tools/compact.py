"""Smaller tool schemas that say the same (token spec §6).

What a model needs to choose and call a tool is kept whole: every parameter name, type, enum, `required`
entry and bound, and the first sentence of every description. What goes is annotation the provider's
function-calling doesn't use: titles, `$schema`, examples, defaults, the `null` branch of an optional
parameter (`required` already says it's optional), and the long tail of a verbose description.
A description is only cut at a real sentence end: never after "e.g.", after a line that introduces a list,
or leaving a stub too short to mean anything. When there is no clean place to cut, it stays whole."""
import re

from ..providers.base import ToolSpec

DROP_KEYS = {"title", "$schema", "examples", "default"}
PARAM_DESCRIPTION_MAX = 200
TOOL_DESCRIPTION_MAX = 300
MIN_SENTENCE = 8  # a shorter "sentence" ("Ok.", "Mode") is a stub, not a description
NULL = {"type": "null"}
_BOUNDARY = re.compile(r"[.!?](?=\s+[A-Z0-9\"'(\[])|\n")  # a sentence end before a new sentence, or a line end
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "approx.")
_LIST_ITEM = re.compile(r"\s*(?:[-*•]|\d+[.)])\s")


def _cuts(text: str) -> list[int]:
    """Where `text` may be cut: the end offsets of its sentences that stand on their own."""
    cuts = []
    for m in _BOUNDARY.finditer(text):
        newline = text[m.start()] == "\n"
        end = m.start() if newline else m.end()
        piece = text[:end].rstrip()
        if len(piece) < MIN_SENTENCE or piece.endswith(":") or piece.lower().endswith(_ABBREVIATIONS):
            continue
        if newline and _LIST_ITEM.match(text, m.end()):  # the line introduces a list that follows
            continue
        cuts.append(end)
    return cuts


def _first_sentence(text: str) -> str:
    text = text.strip()
    cuts = _cuts(text)
    return text[:cuts[0]].strip() if cuts else text


def _compact(node, drop_null: bool = True):
    if isinstance(node, list):
        return [_compact(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in DROP_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            required = node.get("required") or []
            # names are names, never annotations; a required property may still be null, so it keeps that branch
            out[key] = {name: _compact(sub, drop_null=name not in required) for name, sub in value.items()}
        elif key in ("$defs", "definitions") and isinstance(value, dict):
            out[key] = {name: _compact(sub) for name, sub in value.items()}
        elif key == "description" and isinstance(value, str):
            out[key] = _first_sentence(value) if len(value) > PARAM_DESCRIPTION_MAX else value
        elif key in ("items", "additionalProperties", "anyOf", "oneOf", "allOf"):
            out[key] = _compact(value)
        else:
            out[key] = value
    branches = out.get("anyOf")
    if drop_null and isinstance(branches, list) and NULL in branches:
        rest = [b for b in branches if b != NULL]
        if len(rest) == 1 and isinstance(rest[0], dict):
            del out["anyOf"]
            out = {**rest[0], **out}
        elif len(rest) > 1:
            out["anyOf"] = rest
    return out


def compact_schema(schema: dict) -> dict:
    """A compacted copy of a JSON Schema; the argument is never changed."""
    return _compact(schema)


def _short(description: str) -> str:
    first = " ".join(re.split(r"\n\s*\n", description.strip(), maxsplit=1)[0].split())
    if len(first) <= TOOL_DESCRIPTION_MAX:
        return first
    cuts = [c for c in _cuts(first) if c <= TOOL_DESCRIPTION_MAX]
    if cuts:
        return first[:cuts[-1]].strip()
    return first[:TOOL_DESCRIPTION_MAX].rsplit(" ", 1)[0] + "…"


def compact_spec(spec: ToolSpec, override: str | None) -> ToolSpec:
    """The curated description when there is one, else the first paragraph cut at a sentence end."""
    return ToolSpec(spec.name, override if override else _short(spec.description), compact_schema(spec.parameters))
