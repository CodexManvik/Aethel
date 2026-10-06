import json

from aethel.providers.base import ToolSpec
from aethel.tools import desktop, file_commander
from aethel.tools.base import Tool
from aethel.tools.compact import compact_schema, compact_spec
from aethel.tools.descriptions import SHORT
from aethel.tools.registry import ToolRegistry

NOISY = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "thingArguments",
    "type": "object",
    "properties": {
        "path": {"title": "Path", "type": "string"},
        "style": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Style"},
        "mode": {"type": "string", "enum": ["a", "b"], "default": "a", "examples": ["a"]},
        "loc": {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "string"}, {"type": "null"}]},
        "flag": {"anyOf": [{"type": "boolean"}, {"type": "string"}], "default": False},
        "rows": {"type": "array", "title": "Rows",
                 "items": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "number"}]}}},
        "n": {"type": "integer", "minimum": 1, "maximum": 9},
        "opts": {"type": "object", "properties": {"deep": {"title": "Deep", "type": "boolean"}}, "required": ["deep"]},
    },
    "required": ["path", "rows"],
    "additionalProperties": False,
}


def _names(schema):
    """Every property name, recursively, as a path."""
    out = []
    for k, v in (schema.get("properties") or {}).items():
        out.append(k)
        out += [f"{k}.{n}" for n in _names(v)]
        if isinstance(v.get("items"), dict):
            out += [f"{k}[].{n}" for n in _names(v["items"])]
    return out


def test_compaction_keeps_every_property_type_enum_and_required():
    before = json.loads(json.dumps(NOISY))
    after = compact_schema(NOISY)
    assert NOISY == before  # a copy: the original is untouched
    assert _names(after) == _names(NOISY)
    p = after["properties"]
    assert p["path"] == {"type": "string"}
    assert p["style"] == {"type": "string"}  # anyOf [X, null] is X; optional-ness is the "required" list
    assert p["mode"] == {"type": "string", "enum": ["a", "b"]}
    assert p["loc"] == {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "string"}]}
    assert p["flag"] == {"anyOf": [{"type": "boolean"}, {"type": "string"}]}  # a real union stays
    assert p["rows"]["items"]["items"] == {"anyOf": [{"type": "string"}, {"type": "number"}]}
    assert p["n"] == {"type": "integer", "minimum": 1, "maximum": 9}
    assert p["opts"]["required"] == ["deep"] and p["opts"]["properties"]["deep"] == {"type": "boolean"}
    assert after["required"] == ["path", "rows"] and after["type"] == "object"
    assert after["additionalProperties"] is False


def test_schema_annotations_are_dropped():
    after = compact_schema(NOISY)
    blob = json.dumps(after)
    for noise in ("$schema", "title", "examples", "default"):
        assert noise not in blob
    # ...but a property that is *called* "title" survives (it's a name, not an annotation)
    kept = compact_schema({"type": "object", "properties": {"title": {"type": "string", "title": "Title"}},
                           "required": ["title"]})
    assert kept == {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}


def test_a_long_property_description_keeps_its_first_sentence():
    long = "Where to save it. " + "More detail that the model does not need to choose or call. " * 12
    out = compact_schema({"type": "object", "properties": {"p": {"type": "string", "description": long}}})
    assert out["properties"]["p"]["description"] == "Where to save it."
    short = compact_schema({"type": "object", "properties": {"p": {"type": "string", "description": "Absolute path"}}})
    assert short["properties"]["p"]["description"] == "Absolute path"
    nl = compact_schema({"type": "object", "properties": {"p": {"description": "First line\n" + "x " * 200}}})
    assert nl["properties"]["p"]["description"] == "First line"


def test_compact_spec_prefers_the_curated_description_else_cuts_at_a_sentence():
    spec = ToolSpec("t", "Do the thing.   Then   more.\n    Even more words here. " + "tail " * 100, NOISY)
    assert compact_spec(spec, "Curated.").description == "Curated."
    cut = compact_spec(spec, None).description
    assert cut.startswith("Do the thing. Then more.") and len(cut) <= 300  # whitespace collapsed, ends on a sentence
    assert cut.endswith(".")
    assert compact_spec(ToolSpec("t", "Short one.", {}), None).description == "Short one."
    assert compact_spec(spec, None).parameters == compact_schema(NOISY)


def test_every_curated_description_belongs_to_a_real_tool():
    local = {v[0] for v in desktop.EXPOSED.values()} | {v[0] for v in file_commander.EXPOSED.values()}
    local |= {"win_locate"}
    assert set(SHORT) <= local, set(SHORT) - local
    for name, text in SHORT.items():
        assert text.strip() == text and 10 < len(text) <= 600, name
    # the bulky upstream tools are the ones that must be curated
    assert {"win_snapshot", "win_app", "dc_search", "dc_read_file", "dc_edit_block"} <= set(SHORT)


def test_curated_descriptions_still_name_every_parameter_of_the_tool():
    """A short description may not lose a parameter the model needs to know exists."""
    ups = {
        "win_snapshot": ["use_dom", "use_ui_tree", "display", "region"],
        "win_app": ["mode", "name", "executable", "args", "cwd", "window_loc", "window_size"],
        "win_click": ["loc", "button", "clicks"],
        "win_type": ["loc", "clear", "caret_position", "press_enter"],
        "win_scroll": ["loc", "type", "direction", "wheel_times"],
        "win_move": ["loc", "drag", "from_loc", "duration"],
        "win_wait_for": ["condition", "text", "window_name", "timeout", "use_dom"],
        "win_multi_select": ["locs", "press_ctrl"],
        "win_clipboard": ["mode", "text"],
        "dc_read_file": ["offset", "length", "sheet", "range"],
        "dc_search": ["searchType", "filePattern", "literalSearch", "ignoreCase", "maxResults", "contextLines"],
        "dc_edit_block": ["old_string", "new_string", "expected_replacements", "range", "content"],
    }
    for tool, params in ups.items():
        text = SHORT[tool]
        for p in params:
            assert p in text, f"{tool}'s curated text should mention {p}"


def _tool(name="t"):
    async def handler(args, ctx):
        raise AssertionError("not called")
    return Tool(name, "Wait.   For it.", dict(NOISY), "read", handler, lambda a: None)


def test_registry_specs_are_unchanged_by_default_and_compact_ones_are_cached():
    reg = ToolRegistry()
    reg.register(_tool("fs_read"))
    reg.register(_tool("win_wait"))
    plain = reg.specs()
    assert plain[0].parameters == NOISY and plain[0].description == "Wait.   For it."  # untouched
    first = reg.specs(compact=True)
    assert first[0].parameters == compact_schema(NOISY)
    assert first[1].description == SHORT.get("win_wait", "Wait. For it.")
    assert reg.specs(compact=True)[0] is first[0]  # cached per tool: the same object
    assert reg.specs()[0].parameters == NOISY      # compacting never writes back into the tool


def test_registry_cache_follows_a_re_registered_tool():
    reg = ToolRegistry()
    reg.register(_tool("fs_read"))
    old = reg.specs(compact=True)[0]
    reg.unregister("fs_read")
    t = _tool("fs_read")
    t.description = "A new description."
    reg.register(t)
    assert reg.specs(compact=True)[0] is not old
    assert reg.specs(compact=True)[0].description == "A new description."
