"""Smaller tool schemas that say the same thing (token spec §6).

What a model needs to choose and call a tool is kept whole: every parameter name, type, enum, `required`
entry and bound, and the first sentence of every description. What goes is annotation the provider's
function-calling doesn't use: titles, `$schema`, examples, defaults, the `null` branch of an optional
parameter (`required` already says it's optional), and the long tail of a verbose description."""
import re

from ..providers.base import ToolSpec

DROP_KEYS = {"title", "$schema", "examples", "default"}
PARAM_DESCRIPTION_MAX = 200
TOOL_DESCRIPTION_MAX = 300
NULL = {"type": "null"}
_FIRST_SENTENCE = re.compile(r"(?<=\.)\s|\n")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def _first_sentence(text: str) -> str:
    return _FIRST_SENTENCE.split(text.strip(), maxsplit=1)[0].strip()


def _compact(node):
    if isinstance(node, list):
        return [_compact(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in DROP_KEYS:
            continue
        if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
            out[key] = {name: _compact(sub) for name, sub in value.items()}  # names are names, never annotations
        elif key == "description" and isinstance(value, str):
            out[key] = _first_sentence(value) if len(value) > PARAM_DESCRIPTION_MAX else value
        elif key in ("items", "additionalProperties", "anyOf", "oneOf", "allOf"):
            out[key] = _compact(value)
        else:
            out[key] = value
    branches = out.get("anyOf")
    if isinstance(branches, list) and NULL in branches:
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
    window = first[:TOOL_DESCRIPTION_MAX]
    ends = list(_SENTENCE_END.finditer(window))
    return window[:ends[-1].end()] if ends else window.rsplit(" ", 1)[0] + "…"


def compact_spec(spec: ToolSpec, override: str | None) -> ToolSpec:
    """The curated description when there is one, else the first paragraph cut at a sentence end."""
    return ToolSpec(spec.name, override if override else _short(spec.description), compact_schema(spec.parameters))
