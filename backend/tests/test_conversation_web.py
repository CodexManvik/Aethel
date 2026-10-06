import json

import pytest
from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.chat.web_loop import effective_web
from aethel.paths import db_path
from aethel.protocol import Source, Sources, ToolActivity
from aethel.services import build_services
from aethel.settings import AppSettings
from aethel.store.db import Database
from aethel.store.repos import ConversationRepo
from tests.fakes import FakeLocal, factory_from


def _client():
    services = build_services(provider_factory=factory_from({}), local_llm=FakeLocal(up=False))
    return TestClient(create_app(services)), services


def test_a_new_conversation_follows_the_global_setting_and_the_column_exists():
    db = Database(db_path())
    assert "web" in {r["name"] for r in db.query("PRAGMA table_info(conversations)")}
    conv = ConversationRepo(db).create()
    assert conv.web is None
    db.close()


def test_patch_sets_the_web_toggle_as_on_off_or_follow_settings():
    client, _ = _client()
    with client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        url = f"/api/conversations/{cid}"
        assert client.patch(url, json={"web": True}).json()["web"] is True
        assert client.patch(url, json={"web": False}).json()["web"] is False
        assert client.patch(url, json={"web": None}).json()["web"] is None   # null: follow Settings again
        client.patch(url, json={"web": True})
        renamed = client.patch(url, json={"title": "Trip"}).json()
        assert (renamed["title"], renamed["web"]) == ("Trip", True)           # a rename leaves the toggle alone
        both = client.patch(url, json={"title": "Trip 2", "web": False}).json()
        assert (both["title"], both["web"]) == ("Trip 2", False)
        assert client.patch(url, json={}).json()["web"] is False              # nothing to change
        assert client.patch(url, json={"web": "sometimes"}).status_code == 422
        assert client.patch("/api/conversations/missing", json={"web": True}).status_code == 404
        listed = {c["id"]: c for c in client.get("/api/conversations").json()}
        assert listed[cid]["web"] is False


@pytest.mark.parametrize("private,internet,conv,expected", [
    (False, False, None, False), (False, True, None, True),     # follows the global switch
    (False, False, True, True), (False, True, False, False),    # the conversation overrides it
    (True, True, None, False), (True, True, True, False),       # private mode beats everything
    (True, False, True, False),
])
def test_effective_web_truth_table(private, internet, conv, expected):
    settings = AppSettings.model_validate({"private_mode": private, "internet": internet})
    db = Database(db_path())
    repo = ConversationRepo(db)
    c = repo.create()
    repo.set_web(c.id, conv)
    assert effective_web(settings, repo.get(c.id)) is expected
    db.close()


def test_tool_activity_and_sources_events_round_trip():
    src = Source(n=1, title="BBC", url="https://bbc.co.uk/x")
    ev = Sources(message_id="m1", task_id=None, sources=[src])
    again = Sources.model_validate_json(ev.model_dump_json())
    assert again == ev and json.loads(ev.model_dump_json())["type"] == "sources"
    act = ToolActivity(message_id="m1", task_id=None, kind="search", label='Searching "rain"')
    assert json.loads(act.model_dump_json())["type"] == "tool_activity"
    with pytest.raises(Exception):
        ToolActivity(message_id=None, task_id="t", kind="fly", label="x")   # only search or read
