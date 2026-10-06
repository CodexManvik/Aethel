import json
from contextlib import aclosing

import pytest

from aethel.keys import KeyStore
from aethel.paths import db_path
from aethel.providers.base import ChatMessage, StreamDone, TextDelta, ToolSpec, Usage
from aethel.providers.router import RoleRouter
from aethel.settings import SettingsService
from aethel.store.db import Database
from aethel.usage import UsageLog, estimate_breakdown
from tests.fakes import FakeLocal, FakeProvider, ScriptedProvider, factory_from, retryable

pytestmark = pytest.mark.anyio


@pytest.fixture
def db():
    d = Database(db_path())
    yield d
    d.close()


def test_breakdown_splits_the_prompt():
    msgs = [ChatMessage("system", "s" * 400), ChatMessage("user", "u" * 40), ChatMessage("tool", "t" * 800,
                                                                                         tool_call_id="c")]
    tools = [ToolSpec("x", "d" * 100, {"type": "object"})]
    b = estimate_breakdown(msgs, tools)
    assert (b["system"], b["history"], b["observations"]) == (100, 10, 200) and b["tools"] > 25


def test_record_and_totals(db):
    log = UsageLog(db)
    log.record(role="agent", purpose="execute", provider="groq", model="m", ref={"task_id": "t1"},
               usage=Usage(100, 10, 40), breakdown={}, status="ok", latency_ms=5)
    log.record(role="agent", purpose="plan", provider="groq", model="m", ref={"task_id": "t1"},
               usage=None, breakdown={"system": 30, "history": 20}, status="ok", latency_ms=5)
    log.record(role="agent", purpose="plan", provider="groq", model="m", ref={"task_id": "t1"},
               usage=None, breakdown={"system": 30}, status="error", latency_ms=5, started=False)
    assert log.task_totals("t1") == {"prompt": 150, "completion": 10, "cached": 40, "calls": 2, "estimated": True}
    t = log.totals(7)
    assert t["by_purpose"]["execute"]["prompt"] == 100 and t["by_purpose"]["plan"]["calls"] == 1
    assert t["total"]["prompt"] == 150 and len(t["by_day"]) == 1


def test_task_breakdown_lists_each_calls_estimated_parts_in_order(db):
    log = UsageLog(db)
    for purpose, tools, ref in [("plan", 700, "t1"), ("execute", 900, "t1"), ("execute", 400, "t1"),
                                ("execute", 5, "t2")]:
        log.record(role="agent", purpose=purpose, provider="groq", model="m", ref={"task_id": ref},
                   usage=Usage(10, 1, 0), breakdown={"system": 3, "tools": tools}, status="ok", latency_ms=1)
    log.record(role="agent", purpose="execute", provider="groq", model="m", ref={"task_id": "t1"},
               usage=None, breakdown={"tools": 111}, status="error", latency_ms=1, started=False)  # never ran
    assert [b["tools"] for b in log.task_breakdown("t1")] == [900, 400]            # execute calls that ran
    assert [b["tools"] for b in log.task_breakdown("t1", "plan")] == [700]
    assert log.task_breakdown("nope") == []


def _router(db, providers, chain):
    settings = SettingsService(db)
    settings.update({"roles": {"chat": [{"provider": "groq", "model": m} for m in chain]}})
    keys = KeyStore()
    keys.set_many({"groq": "k"})
    log = UsageLog(db)
    return RoleRouter(settings=settings, keys=keys, local=FakeLocal(), factory=factory_from(providers), usage=log), log


def rows(db):
    return [dict(r) for r in db.query("SELECT * FROM llm_calls ORDER BY rowid")]


async def test_router_records_purpose_ref_and_usage(db):
    p = ScriptedProvider([[TextDelta("hi"), StreamDone("stop", Usage(12, 3))]])
    router, _ = _router(db, {"groq:a": p}, ["a"])
    [e async for e in router.stream("chat", [ChatMessage("user", "hi")], purpose="fact_extract",
                                    ref={"message_id": "m1"})]
    [r] = rows(db)
    assert (r["purpose"], r["message_id"], r["prompt_tokens"], r["completion_tokens"], r["status"], r["estimated"]) \
        == ("fact_extract", "m1", 12, 3, "ok", 0)
    assert json.loads(r["breakdown"])["history"] >= 0


async def test_failover_records_both_attempts(db):
    router, _ = _router(db, {"groq:a": FakeProvider(error=retryable()), "groq:b": FakeProvider(chunks=["ok"])},
                        ["a", "b"])
    [e async for e in router.stream("chat", [ChatMessage("user", "hi")])]
    assert [(r["model"], r["status"], r["prompt_tokens"]) for r in rows(db)][0] == ("a", "error", None)
    second = rows(db)[1]
    assert (second["model"], second["status"], second["estimated"]) == ("b", "ok", 1)


async def test_closing_early_records_cancelled(db):
    router, _ = _router(db, {"groq:a": FakeProvider(chunks=["a", "b", "c"])}, ["a"])
    stream = router.stream("chat", [ChatMessage("user", "hi")])
    async with aclosing(stream):
        async for _ in stream:
            break
    assert [r["status"] for r in rows(db)] == ["cancelled"]


def test_a_chat_turn_and_a_task_are_recorded():
    from fastapi.testclient import TestClient

    from aethel.app import create_app
    from aethel.services import build_services
    from tests.fakes import tool_call

    agent = ScriptedProvider([
        [tool_call("submit_plan", steps=["Say done"], checks=[])],
        [tool_call("finish_task", summary="Done.")],
    ])
    svc = build_services(provider_factory=factory_from({"groq:g": FakeProvider(chunks=["hi"]), "groq:a": agent}),
                         local_llm=FakeLocal(fail="x"))
    svc.keys.set_many({"groq": "k"})
    svc.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}],
                                   "agent": [{"provider": "groq", "model": "a"}]}})
    client = TestClient(create_app(svc))
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "hello"})
            while ws.receive_json()["type"] != "message_end":
                pass
            ws.send_json({"type": "start_task", "conversation_id": conv["id"], "goal": "say done"})
            while True:
                ev = ws.receive_json()
                if ev["type"] == "task_state" and ev["state"] in ("done", "failed"):
                    break
    got = [(r["purpose"], bool(r["message_id"]), bool(r["task_id"])) for r in
           svc.db.query("SELECT * FROM llm_calls ORDER BY rowid")]
    assert got[0] == ("chat_reply", True, False)
    assert ("plan", False, True) in got and ("execute", False, True) in got


def test_usage_endpoints():
    from fastapi.testclient import TestClient

    from aethel.app import create_app
    from aethel.services import build_services

    svc = build_services(provider_factory=factory_from({}), local_llm=FakeLocal(fail="x"))
    client = TestClient(create_app(svc))
    with client:
        conv = svc.conversations.create()
        task = svc.tasks.create(conv.id, "x")
        svc.usage.record(role="agent", purpose="execute", provider="groq", model="m", ref={"task_id": task.id},
                         usage=Usage(1000, 50, 200), breakdown={}, status="ok", latency_ms=1)
        assert client.get(f"/api/tasks/{task.id}/usage").json() == \
            {"prompt": 1000, "completion": 50, "cached": 200, "calls": 1, "estimated": False}
        assert client.get("/api/tasks/task_nope/usage").status_code == 404
        body = client.get("/api/usage", params={"days": 7}).json()
        assert body["by_purpose"]["execute"]["prompt"] == 1000 and body["days"] == 7
        assert client.get("/api/usage", params={"days": 0}).status_code == 422
