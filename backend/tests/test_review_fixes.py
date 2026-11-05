"""Regressions found by the full review of main (2026-11-05)."""
import json
import os
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aethel import auth
from aethel.app import create_app
from aethel.memory.rsm import KnowledgeStore
from aethel.providers.base import ChatMessage, ProviderError, ToolCallsReady
from aethel.providers.openai_compat import OpenAICompatProvider
from aethel.providers.router import LOCAL_TIMEOUT_S, make_provider_factory
from aethel.runtime import macro
from aethel.runtime.checks import Check, run_check, run_checks_with
from aethel.runtime.engine import _parse_plan
from aethel.runtime.reflect import task_report
from aethel.runtime.store import StepRecord, TaskRecord
from aethel.safety.changes import ChangeLog
from aethel.safety.permissions import Permissions
from aethel.services import build_services, local_ports
from aethel.settings import AppSettings, RouteEntry, SettingsService
from aethel.store.db import Database
from aethel.paths import aethel_home, db_path
from aethel.tools.base import Assessment, Tool, ToolContext, ToolResult
from aethel.tools.desktop import IRREVERSIBLE_KEYS, _pressed
from aethel.tools.local_fs import MAX_LIST, fs_tools
from aethel.tools.paths import real_path, resolve_user_path
from aethel.tools.shell import scrubbed_env, shell_tool
from tests.fakes import FakeLocal, factory_from, tool_call
from tests.test_engine import h  # noqa: F401  (h is a fixture)
from tests.test_macro_run import REFLECT_ONLY, macro_engine  # noqa: F401  (a fixture)
from tests.test_rsm import fake_embed

windows = pytest.mark.skipif(os.name != "nt", reason="Windows behaviour")


# ---- shell ------------------------------------------------------------------------------------------------------
@windows
@pytest.mark.anyio
async def test_a_quoted_path_reaches_cmd_as_written(tmp_path):
    folder = tmp_path / "has a space"
    folder.mkdir()
    (folder / "found.txt").write_text("x")
    run = shell_tool(Permissions(tmp_path / "p.yaml")).handler
    result = await run({"command": f'dir /b "{folder}"'}, ToolContext(task_id=None))
    assert result.ok and "found.txt" in result.content


def test_more_kinds_of_credential_are_kept_from_commands(monkeypatch):
    for name in ("DB_PASSWORD", "GITHUB_PAT", "AWS_CREDENTIALS"):
        monkeypatch.setenv(name, "secret")
    assert not {"DB_PASSWORD", "GITHUB_PAT", "AWS_CREDENTIALS"} & {k.upper() for k in scrubbed_env()}


# ---- settings that must never stop the app starting -----------------------------------------------------------------
@pytest.mark.parametrize("url", ["http://localhost:abc/v1", "http://localhost:99999", "http://[abc/v1"])
def test_an_address_with_a_bad_host_or_port_is_refused_when_saved(url):
    with pytest.raises(ValidationError):
        AppSettings(endpoints=[{"id": "lm", "name": "LM", "base_url": url}])


def test_a_bad_address_saved_by_an_older_version_is_cleared_not_fatal():
    db = Database(db_path())
    service = SettingsService(db)
    service._put(service.KEY, json.dumps({
        "endpoints": [{"id": "lm", "name": "LM", "base_url": "http://localhost:abc/v1"}],
        "images": {"comfyui": {"base_url": "http://localhost:abc"}, "openai": {"base_url": "http://[abc"}}}))
    saved = service.get()
    assert saved.endpoints[0].base_url == "" and saved.images.comfyui.base_url == "" and saved.images.openai.base_url == ""
    assert local_ports(None, saved) == [8080]


@pytest.mark.parametrize("legacy", ['{"gpu_layers": -1}', '{"threads": "lots"}', '[1, 2]'])
def test_odd_legacy_settings_never_stop_the_app_starting(tmp_path, legacy):
    path = tmp_path / "settings.json"
    path.write_text(legacy, encoding="utf-8")
    service = SettingsService(Database(db_path()))
    service.import_legacy(path)  # doesn't raise
    assert service.get().local_llm.gpu_layers >= 0


# ---- paths ----------------------------------------------------------------------------------------------------------
@windows
def test_a_network_path_is_never_opened_to_check_it(monkeypatch, tmp_path):
    def touched(*_a, **_k):
        raise AssertionError("realpath would connect to the host")
    monkeypatch.setattr(os.path, "realpath", touched)
    path = "\\\\evil.example\\share\\x.txt"
    assert str(resolve_user_path(path)) == path
    assert real_path("//evil.example/share/x.txt") == path
    monkeypatch.undo()
    assert Permissions(tmp_path / "p.yaml").check_path(path, "read").verdict == "ask"


@windows
def test_a_check_cannot_name_another_computer():
    with pytest.raises(ValidationError):
        Check(kind="file_exists", path="\\\\evil.example\\share\\x.txt")


# ---- providers ------------------------------------------------------------------------------------------------------
def _sse(*chunks) -> httpx.Response:
    body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _call(index, call_id, name, arguments):
    fn = {"name": name, "arguments": arguments} if name else {"arguments": arguments}
    tc = {"index": index, "type": "function", "function": fn, **({"id": call_id} if call_id else {})}
    return {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
            "choices": [{"index": 0, "delta": {"tool_calls": [tc]}, "finish_reason": None}]}


def _provider(handler):
    return OpenAICompatProvider(provider="gemini", base_url="https://example.test/v1", api_key="k", model="m",
                                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.anyio
async def test_parallel_calls_numbered_the_same_stay_separate():
    provider = _provider(lambda r: _sse(_call(0, "a", "fs_read", '{"path":'), _call(0, None, None, '"x"}'),
                                        _call(0, "b", "fs_list", '{"path":'), _call(0, None, None, '"y"}')))
    events = [e async for e in provider.stream([ChatMessage("user", "hi")], temperature=0, max_tokens=8)]
    calls = next(e for e in events if isinstance(e, ToolCallsReady)).calls
    assert [(c.name, c.arguments) for c in calls] == [("fs_read", '{"path":"x"}'), ("fs_list", '{"path":"y"}')]


@pytest.mark.anyio
async def test_an_error_inside_the_stream_is_a_provider_error():
    provider = _provider(lambda r: _sse({"error": {"message": "upstream fell over"}}))
    with pytest.raises(ProviderError, match="upstream fell over"):
        [e async for e in provider.stream([ChatMessage("user", "hi")], temperature=0, max_tokens=8)]


def test_a_server_on_this_pc_skips_the_proxy_and_gets_time_to_think():
    cloud, local = httpx.AsyncClient(), httpx.AsyncClient(trust_env=False)
    factory = make_provider_factory(cloud, local)
    settings = AppSettings(endpoints=[{"id": "lm", "name": "LM", "base_url": "http://127.0.0.1:1234/v1"}])
    here = factory(RouteEntry(provider="lm", model="m"), "k", settings)
    away = factory(RouteEntry(provider="groq", model="m"), "k", settings)
    assert here._client._client is local and here._client.timeout == LOCAL_TIMEOUT_S
    assert away._client._client is cloud


# ---- macros ---------------------------------------------------------------------------------------------------------
def test_a_macro_parameter_keeps_the_goals_capitals():
    assert macro.bind("Email {p1} about lunch", "email John Smith about lunch.") == {"p1": "John Smith"}


def test_a_macro_parameter_is_encoded_in_a_url():
    filled = macro.fill({"url": "https://x.test/?q={p1+}", "text": "{p1}"}, {"p1": "rock & roll #1"})
    assert filled == {"url": "https://x.test/?q=rock+%26+roll+%231", "text": "rock & roll #1"}


@pytest.mark.anyio
async def test_a_macro_step_that_never_ran_hands_over_to_the_model(h, macro_engine):  # noqa: F811
    engine, provider, skill, _ = macro_engine([[tool_call("finish_task", summary="Done by hand.")], *REFLECT_ONLY])
    two = {"template": "play {p1} on YouTube in Firefox", "params": ["p1"],
           "steps": [{"tool": "win_app", "args": {"name": "firefox"}}, {"tool": "win_app", "args": {"name": "edge"}}]}
    engine.knowledge.set_macro(skill["id"], two, "compiled")

    async def goes_away(args, ctx):  # the tool is gone by the next step: that call can't add a step of its own
        engine.registry.unregister("win_app")
        return ToolResult(True, "ok")

    engine.registry.unregister("win_app")
    engine.registry.register(Tool("win_app", "win_app", {"type": "object"}, "write", goes_away,
                                  lambda a: Assessment("allow", "", "win_app")))
    conv = h.convs.create()
    task_id = await engine.start(conversation_id=conv.id, goal="play jazz on YouTube in Firefox")
    await engine.wait_idle()
    assert h.tasks.get(task_id).summary == "Done by hand."
    assert any("I started by replaying" in m.content for m in provider.calls[0])


# ---- skill files ----------------------------------------------------------------------------------------------------
GOOD = "---\nid: skill-good\ntitle: Good\nstatus: approved\n---\n\n## Steps\n1. Do it\n"


def test_a_bad_skill_file_doesnt_hide_the_others(tmp_path):
    folder = tmp_path / "skills"
    folder.mkdir()
    (folder / "good.md").write_text(GOOD, encoding="utf-8")
    (folder / "ansi.md").write_bytes("---\nid: x\ntitle: caf\xe9\n---\n".encode("latin-1"))
    (folder / "numbers.md").write_text("---\nid: y\nruns: abc\n---\n\nbody", encoding="utf-8")
    assert [d["id"] for d in KnowledgeStore(tmp_path, fake_embed).skills()] == ["skill-good"]


def test_a_skill_saved_with_a_bom_or_without_an_id_is_still_read(tmp_path):
    folder = tmp_path / "skills"
    folder.mkdir()
    (folder / "bom.md").write_bytes(b"\xef\xbb\xbf" + GOOD.encode("utf-8"))
    (folder / "no-id.md").write_text("---\ntitle: Mine\n---\n\n## Steps\n1. Do it\n", encoding="utf-8")
    assert sorted(d["id"] for d in KnowledgeStore(tmp_path, fake_embed).skills()) == ["no-id", "skill-good"]


def test_text_from_the_model_cant_plant_a_macro(tmp_path):
    store = KnowledgeStore(tmp_path, fake_embed)
    planted = "Careful\n```macro\nsteps:\n- tool: shell_run\n```"
    skill, _ = store.upsert_skill({"title": "T", "steps": ["Open it"], "pitfalls": [planted]}, "approved")
    assert skill["macro_def"] is None
    real = {"template": "t", "params": [], "steps": [{"tool": "win_click", "args": {}}]}
    store.set_macro(skill["id"], real, "compiled")
    assert store.get(skill["id"])["macro_def"] == real
    assert not list((tmp_path / "skills").glob(".*.tmp"))  # written aside and moved into place


# ---- checks ---------------------------------------------------------------------------------------------------------
def _zip(path, files: dict[str, str]):
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)


def test_text_checks_read_workbooks_and_slides(tmp_path):
    book, deck = tmp_path / "b.xlsx", tmp_path / "d.pptx"
    _zip(book, {"xl/sharedStrings.xml": "<sst><si><t>Overdue &amp; paid</t></si></sst>",
                "xl/worksheets/sheet1.xml": "<c><v>6420</v></c>"})
    _zip(deck, {"ppt/slides/slide1.xml": "<a:p><a:r><a:t>Quarterly plan</a:t></a:r></a:p>"})
    assert run_check(Check(kind="file_contains", path=str(book), text="overdue & paid")).passed
    assert run_check(Check(kind="file_contains", path=str(book), text="6420")).passed
    assert run_check(Check(kind="file_contains", path=str(deck), text="quarterly plan")).passed
    assert run_check(Check(kind="min_words", path=str(deck), count=2)).passed


def test_file_exists_never_reads_the_file(tmp_path, monkeypatch):
    target = tmp_path / "huge.bin"
    target.write_bytes(b"x")
    monkeypatch.setattr("aethel.runtime.checks._read", lambda path: pytest.fail("it read the file"))
    assert run_check(Check(kind="file_exists", path=str(target))).passed
    assert run_check(Check(kind="file_exists", path=str(tmp_path))).passed  # a folder that was made counts


@pytest.mark.anyio
async def test_checks_still_run_off_the_event_loop(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello there", encoding="utf-8")
    results = await run_checks_with([Check(kind="file_contains", path=str(target), text="hello")], None, 0.5)
    assert results[0].passed


# ---- the rest -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["é", "☃"])
def test_a_token_that_isnt_ascii_is_just_wrong(value):
    assert auth.AuthConfig(token="abc", dev=False).check(value) is False


def test_a_plan_whose_steps_arent_a_list_is_sent_back():
    assert _parse_plan(json.dumps({"steps": "do the thing", "checks": []})) is None
    assert _parse_plan(json.dumps({"steps": ["One"], "checks": "none"})) == (["One"], [])


def test_a_blank_result_doesnt_lose_the_reflection():
    task = TaskRecord(id="t", conversation_id="c", goal="g", state="done", plan=[], plan_done=[], checks=[], summary=None,
                      error=None, created_at="", updated_at="")
    step = StepRecord(id="s", task_id="t", idx=0, tool="fs_read", args={}, summary="x", verdict="allow", ok=True,
                      result="  \n ", duration_ms=1, created_at="")
    assert "fs_read" in task_report(task, [step], lambda _name, text: text)


@pytest.mark.parametrize("spelled", ["control+w", "w+ctrl", "CTRL + W"])
def test_a_shortcut_is_judged_by_its_keys(spelled):
    assert _pressed(spelled) in IRREVERSIBLE_KEYS


@pytest.mark.parametrize("spelled", ["windows+r", "lwin+r", "super+r", "WIN + R"])
def test_every_name_for_the_windows_key_counts(spelled):
    assert "win" in _pressed(spelled)


@pytest.mark.anyio
async def test_a_listing_survives_a_file_it_cant_look_at_and_says_when_it_stops(tmp_path, monkeypatch):
    for i in range(MAX_LIST + 3):
        (tmp_path / f"f{i:03}.txt").write_text("x")
    listing = fs_tools(Permissions(tmp_path / "p.yaml"), None)[0].handler
    real_stat = type(tmp_path).stat

    def stat(self, **kw):
        if self.name == "f000.txt":
            raise PermissionError(13, "locked")
        return real_stat(self, **kw)

    monkeypatch.setattr(type(tmp_path), "stat", stat)
    result = await listing({"path": str(tmp_path)}, ToolContext(task_id=None))
    assert result.ok and "f000.txt  (can't be read)" in result.content
    assert result.content.endswith("more not shown]")


def test_undo_puts_back_the_folder_too(tmp_path):
    db = Database(db_path())
    log = ChangeLog(db)
    target = tmp_path / "gone" / "a.txt"
    target.parent.mkdir()
    target.write_text("before", encoding="utf-8")
    change = log.record_before_write(str(target), None)
    target.unlink()
    target.parent.rmdir()
    assert log.rollback(change)[0] and target.read_text(encoding="utf-8") == "before"


def _client():
    services = build_services(provider_factory=factory_from({}), local_llm=FakeLocal(up=False))
    return TestClient(create_app(services)), services


def test_private_mode_stops_the_gemini_listing_and_the_face_download():
    client, svc = _client()
    svc.settings.update({"private_mode": True})
    with client:
        assert client.get("/api/images/gemini-models").status_code == 409
        assert client.post("/api/images/face-models").status_code == 409


def test_deleting_a_conversation_takes_what_its_tasks_left_behind():
    client, svc = _client()
    with client:
        conv = svc.conversations.create()
        other = svc.conversations.create()
        mine, kept = svc.tasks.create(conv.id, "goal"), svc.tasks.create(other.id, "goal")
        for task in (mine, kept):
            picture = aethel_home() / "media" / "tasks" / task.id / "s.jpg"
            picture.parent.mkdir(parents=True)
            picture.write_bytes(b"x")
            svc.db.execute("INSERT INTO file_changes (id, task_id, path, existed, before, created_at)"
                           " VALUES (?, ?, 'p', 1, ?, 'now')", (f"chg-{task.id}", task.id, b"secret"))
        assert client.delete(f"/api/conversations/{conv.id}").status_code == 204
        assert not (aethel_home() / "media" / "tasks" / mine.id).exists()
        assert (aethel_home() / "media" / "tasks" / kept.id).exists()
        assert [r["task_id"] for r in svc.db.query("SELECT task_id FROM file_changes")] == [kept.id]
        assert client.delete(f"/api/conversations/{conv.id}").status_code == 404


# ---- the second pass ----------------------------------------------------------------------------------------------
class _FakeOffice:
    def __init__(self):
        self.ActiveWorkbook = self
        self.alerts_when_saving = None
        self.DisplayAlerts = True

    def SaveAs(self, path):
        self.alerts_when_saving = self.DisplayAlerts


def test_office_never_runs_macros_and_saving_never_stops_to_ask(monkeypatch):
    pytest.importorskip("win32com")
    office = pytest.importorskip("aethel.mcp_servers.office")
    fake = _FakeOffice()
    monkeypatch.setattr(office.win32com.client, "Dispatch", lambda _progid: fake)
    assert office.excel_save("C:/x.xlsx") == "Saved C:/x.xlsx."
    assert fake.AutomationSecurity == office.MSO_AUTOMATION_SECURITY_FORCE_DISABLE
    assert fake.alerts_when_saving is False and fake.DisplayAlerts is True


def test_a_persona_file_with_a_yaml_typo_is_skipped_and_a_bom_is_fine(tmp_path):
    from aethel.personas.store import PersonaStore
    store = PersonaStore(tmp_path)
    store.ensure_builtin()
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "persona.md").write_text("---\nname: [unclosed\n---\n", encoding="utf-8")
    (tmp_path / "bom").mkdir()
    (tmp_path / "bom" / "persona.md").write_bytes(b"\xef\xbb\xbf---\nname: Mira\nanchors: [portraits/anchor.jpg]\n---\n")
    assert sorted(p.id for p in store.list()) == ["aethel", "bom"]
    assert store.get("broken") is None
    assert store.get("bom").anchors == ["portraits/anchor.jpg"]


@pytest.mark.anyio
async def test_system_1_is_tried_again_after_a_failed_load():
    from aethel.system1.service import System1
    settings = SettingsService(Database(db_path()))
    attempts = []

    def load(_folder):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("offline")
        return object()

    s1 = System1(settings.db, settings, model_dir=aethel_home(), loader=load, download=lambda _d: None)
    s1.start()
    await s1._starting
    assert s1.status().startswith("failed")
    s1.start()
    await s1._starting
    assert s1.status() == "ready"
    first = s1._starting
    s1.start()  # loaded: nothing more to do
    assert s1._starting is first


def test_a_cut_reply_keeps_a_photo_that_landed_meanwhile():
    from types import SimpleNamespace
    from aethel.store.repos import ConversationRepo, MessageRepo
    from aethel.voice.session import VoiceSession, _Turn
    db = Database(db_path())
    conv = ConversationRepo(db).create()
    messages = MessageRepo(db)
    reply = messages.add(conv.id, "assistant", "One. Two.")
    session = VoiceSession(SimpleNamespace(messages=messages, db=db), None, None)
    turn = _Turn("t", 0.0, 0.0, None, 0, transcript_at=0.0)
    turn.chat, turn.assistant_id, turn.texts, turn.heard_upto = True, reply.id, ["One."], 0

    original = messages.get

    def stale_then_real(msg_id):  # what a slow read sees: a photo arrives just after it
        found = original(msg_id)
        messages.merge_meta(msg_id, lambda m: {**m, "images": [{"id": "p"}]})
        return found

    messages.get = stale_then_real
    assert session._cut(turn) == "One."
    messages.get = original
    saved = messages.get(reply.id)
    assert saved.content == "One." and saved.status == "stopped"
    assert saved.meta["interrupted"] is True and saved.meta["images"] == [{"id": "p"}]
