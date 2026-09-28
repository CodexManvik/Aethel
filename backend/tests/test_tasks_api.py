import os

import yaml
from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.safety.permissions import default_manifest
from aethel.services import build_services
from tests.fakes import FakeLocal, FakeProvider, ScriptedProvider, factory_from, tool_call


def _client(tmp_path, turns):
    manifest = default_manifest()
    manifest["filesystem"]["allowed_read_paths"] = [str(tmp_path)]
    manifest["filesystem"]["allowed_write_paths"] = [str(tmp_path / "out")]
    perms = tmp_path / "permissions.yaml"
    perms.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    scripted = ScriptedProvider(turns)
    services = build_services(provider_factory=factory_from({"groq:g": scripted, "groq:c": FakeProvider()}),
                              local_llm=FakeLocal(), permissions_path=perms)
    services.keys.set_many({"groq": "k"})
    services.settings.update({"roles": {"agent": [{"provider": "groq", "model": "g"}],
                                        "chat": [{"provider": "groq", "model": "c"}]}})
    return TestClient(create_app(services)), services


def _until(ws, predicate, limit=60):
    events = []
    for _ in range(limit):
        ev = ws.receive_json()
        events.append(ev)
        if predicate(ev):
            return events
    raise AssertionError("never saw the expected event")


def test_task_over_websocket_with_approval_and_rollback(tmp_path):
    inside = str(tmp_path / "out" / "poem.txt")
    outside = str(tmp_path / "desk.txt")
    client, svc = _client(tmp_path, [
        [tool_call("submit_plan", steps=["Write", "Copy"], checks=[{"kind": "file_exists", "path": outside}])],
        [tool_call("fs_write", path=inside, content="rain"), tool_call("fs_write", path=outside, content="rain")],
        [tool_call("finish_task", summary="Both copies are saved.")],
    ])
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "start_task", "conversation_id": conv["id"], "goal": "haiku", "client_id": "k1"})
            events = _until(ws, lambda e: e["type"] == "approval_needed")
            created = next(e for e in events if e["type"] == "task_created")
            assert created["client_id"] == "k1"
            approval = events[-1]
            assert approval["summary"] == outside
            ws.send_json({"type": "approval_decision", "approval_id": approval["approval_id"], "decision": "allow_once"})
            done = _until(ws, lambda e: e["type"] == "task_state" and e["state"] in ("done", "failed"))[-1]
        assert done["state"] == "done" and done["message_text"] == "Both copies are saved."
        [detail] = client.get("/api/tasks", params={"conversation_id": conv["id"]}).json()
        assert detail["task"]["state"] == "done"
        assert [s["tool"] for s in detail["steps"]] == ["fs_write", "fs_write"]
        assert detail["check_descriptions"] == [f"{outside} exists"]
        assert client.get(f"/api/tasks/{created['task_id']}").json()["task"]["id"] == created["task_id"]
        assert client.get("/api/tasks/nope").status_code == 404
        rolled = client.post(f"/api/tasks/{created['task_id']}/rollback").json()
        assert all(r["ok"] for r in rolled["results"])
    assert not os.path.exists(inside) and not os.path.exists(outside)


def test_task_control_cancel_over_websocket(tmp_path):
    client, svc = _client(tmp_path, [
        [tool_call("submit_plan", steps=["Write"], checks=[])],
        [tool_call("fs_write", path=str(tmp_path / "x.txt"), content="x")],
    ])
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "start_task", "conversation_id": conv["id"], "goal": "x"})
            events = _until(ws, lambda e: e["type"] == "approval_needed")
            task_id = events[-1]["task_id"]
            pending = client.get(f"/api/tasks/{task_id}").json()["approvals"]
            assert [a["approval_id"] for a in pending] == [events[-1]["approval_id"]]
            assert client.post(f"/api/tasks/{task_id}/rollback").status_code == 409
            ws.send_json({"type": "task_control", "task_id": task_id, "action": "cancel"})
            final = _until(ws, lambda e: e["type"] == "task_state" and e["state"] == "cancelled")[-1]
    assert final["message_text"] == "Okay, I've stopped that task."


def test_kill_switch_cancels_every_running_task(tmp_path):
    client, svc = _client(tmp_path, [
        [tool_call("submit_plan", steps=["Write"], checks=[])],
        [tool_call("fs_write", path=str(tmp_path / "x.txt"), content="x")],
        [tool_call("submit_plan", steps=["Write"], checks=[])],
        [tool_call("fs_write", path=str(tmp_path / "y.txt"), content="y")],
    ])
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "start_task", "conversation_id": conv["id"], "goal": "one"})
            _until(ws, lambda e: e["type"] == "approval_needed")
            ws.send_json({"type": "start_task", "conversation_id": conv["id"], "goal": "two"})
            _until(ws, lambda e: e["type"] == "approval_needed")
            ws.send_json({"type": "kill_switch"})
            seen = set()
            _until(ws, lambda e: (e["type"] == "task_state" and e["state"] == "cancelled"
                                  and seen.add(e["task_id"]) is None and len(seen) == 2))
    assert all(t.state == "cancelled" for t in svc.tasks.list_for_conversation(conv["id"]))


def test_tools_status_lists_servers_and_tools(tmp_path):
    client, svc = _client(tmp_path, [])
    with client:
        body = client.get("/api/tools").json()
    assert body["servers"] == {}  # tests run without MCP servers
    assert "fs_write" in body["tools"] and "shell_run" in body["tools"]
