import json

from fastapi.testclient import TestClient
from PIL import Image

from aethel.app import create_app
from aethel.personas.draft import DraftError, parse_draft
from aethel.services import build_services
from tests.fakes import FakeLocal, FakeProvider, factory_from

DRAFT = {"name": "Mira", "image_style": "soft film photo", "personality": "You are Mira, curious and dry.",
         "backstory": "Grew up by the sea.", "speaking_style": "short lowercase bursts", "boundaries": "kind"}
WHATSAPP = "\n".join(f"12/05/23, 14:3{i % 10} - {'Rosie' if i % 3 else 'Sam'}: {'lol same honestly' if i % 3 else 'ok then'}"
                     for i in range(30))


def _client(provider=None):
    svc = build_services(provider_factory=factory_from({"groq:g": provider or FakeProvider()}),
                         local_llm=FakeLocal(fail="none"))
    svc.keys.set_many({"groq": "k"})
    svc.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}})
    return TestClient(create_app(svc)), svc


def test_parse_draft_tolerates_fences_and_chatter():
    p = parse_draft("Sure! ```json\n" + json.dumps(DRAFT) + "\n```")
    assert p.name == "Mira" and p.section("Speaking style") == "short lowercase bursts" and p.image_style
    for bad in ("no json here", "{broken", json.dumps({"name": "x"})):
        try:
            parse_draft(bad)
            raise AssertionError(bad)
        except DraftError:
            pass


def test_draft_with_a_scripted_llm_and_a_retry():
    provider = FakeProvider(chunks=["not json"])
    client, svc = _client(provider)
    with client:
        assert client.post("/api/personas/draft", json={"description": "a sailor"}).status_code == 502
        provider.chunks = [json.dumps(DRAFT)]
        got = client.post("/api/personas/draft", json={"description": "a curious sailor"}).json()
    assert got["name"] == "Mira" and got["sections"]["Personality"].startswith("You are Mira")
    assert [p.id for p in svc.personas.list()] == ["aethel"]  # a draft is never saved
    assert provider.calls[-1][0].role == "system" and provider.calls[-1][1].content == "a curious sailor"


def test_crud(tmp_path):
    client, svc = _client()
    with client:
        listed = client.get("/api/personas").json()
        assert [p["id"] for p in listed] == ["aethel"] and listed[0]["built_in"]
        made = client.post("/api/personas", json={"name": "Mira", "sections": {"Personality": "Curious."},
                                                  "voice": {"blend": {"af_heart": 1}, "speed": 1.1}}).json()
        assert made["id"] == "mira" and made["sections"]["Boundaries"] == "" and made["lines"] == 0
        patched = client.patch("/api/personas/mira", json={"sections": {"Backstory": "Sea."}, "accent_pick": "#3366cc",
                                                            "image_frequency": "sometimes"}).json()
        assert patched["sections"]["Personality"] == "Curious." and patched["sections"]["Backstory"] == "Sea."
        assert patched["accent"]["source"] == "manual" and patched["image_frequency"] == "sometimes"
        assert client.patch("/api/personas/mira", json={"accent_pick": None}).json()["accent"] is None
        assert client.patch("/api/personas/mira", json={"image_frequency": "always"}).status_code == 422
        assert client.patch("/api/personas/nobody", json={}).status_code == 404
        assert client.patch("/api/personas/mira", json={"sections": {"Personality": "x" * 6001}}).status_code == 422
        extra = {f"Extra {i}": "x" for i in range(9)}  # 4 standard + 9 > 12
        assert client.patch("/api/personas/mira", json={"sections": extra}).status_code == 422
        assert client.patch("/api/personas/mira", json={"sections": {"Bad\nname": "x"}}).status_code == 422
        assert client.delete("/api/personas/aethel").status_code == 409
        assert client.delete("/api/personas/mira").status_code == 204
        assert client.get("/api/personas/mira").status_code == 404


def test_files_only_pictures_inside_the_folder():
    client, svc = _client()
    with client:
        client.post("/api/personas", json={"name": "Mira"})
        folder = svc.personas.folder("mira")
        (folder / "portraits").mkdir()
        Image.new("RGB", (4, 4), (200, 10, 10)).save(folder / "portraits" / "anchor.jpg")
        (svc.personas.root / "secret.png").write_bytes(b"x")
        ok = client.get("/api/personas/mira/files/portraits/anchor.jpg")
        assert ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg"
        for path in ("persona.md", "../secret.png", "..%2Fsecret.png", "portraits/missing.jpg", ".persona.md.tmp"):
            assert client.get(f"/api/personas/mira/files/{path}").status_code == 404, path


def test_style_import_flow():
    client, svc = _client()
    with client:
        client.post("/api/personas", json={"name": "Rosie"})
        first = client.post("/api/personas/rosie/style/import", json={"filename": "chat.txt", "content": WHATSAPP}).json()
        assert first["speakers"][0] == {"name": "Rosie", "messages": 20} and first["directive"] is None
        second = client.post("/api/personas/rosie/style/import",
                             json={"filename": "chat.txt", "content": WHATSAPP, "speaker": "Rosie"}).json()
        assert second["messages"] == 20 and "Rosie writes" in second["directive"] and second["import_id"]
        saved = client.put("/api/personas/rosie/style", json={"directive": "Edited directive.",
                                                                "import_id": second["import_id"]}).json()
        assert saved["style"] == "Edited directive." and saved["lines"] == 1  # 20 identical lines, deduplicated
        assert client.put("/api/personas/rosie/style", json={"directive": "x", "import_id": "stale"}).status_code == 409
        assert client.get("/api/personas/rosie").json()["style"] == "Edited directive."  # nothing written on a 409
        bad = client.post("/api/personas/rosie/style/import", json={"content": "{nope"})
        assert bad.status_code == 422
        assert client.delete("/api/personas/rosie/style").status_code == 204
        after = client.get("/api/personas/rosie").json()
        assert after["style"] is None and after["lines"] == 0


def _turn(ws, conv_id, text):
    ws.send_json({"type": "user_message", "conversation_id": conv_id, "text": text})
    events = []
    while (ev := ws.receive_json())["type"] != "message_end":
        events.append(ev)
    return events


def test_companion_chat_gets_tone_and_style_examples():
    provider = FakeProvider(chunks=["yay"])
    client, svc = _client(provider)
    with client:
        client.post("/api/personas", json={"name": "Rosie"})
        conv = client.post("/api/conversations", json={"persona_id": "rosie"}).json()
        imported = client.post("/api/personas/rosie/style/import", json={
            "content": "12/05/23, 14:30 - Rosie: the canal pub is honestly the best pub\n12/05/23, 14:31 - Sam: ok",
            "speaker": "Rosie"}).json()
        client.put("/api/personas/rosie/style", json={"directive": "", "import_id": imported["import_id"]})
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, conv["id"], "this is amazing, thank you so much!")
            tone = ws.receive_json()
            assert tone["type"] == "tone_changed" and tone["mood"] > 0 and tone["conversation_id"] == conv["id"]
            _turn(ws, conv["id"], "honestly is the canal pub the best pub")
    second = provider.calls[1][0].content
    assert "### How Rosie tends to put things" in second and "the canal pub is honestly the best pub" in second
    assert "The conversation's tone right now:" in second
    assert second.index("tone right now") < second.index("Current local time")
    assert svc.conversations.meta(conv["id"])["tone"]["mood"] > 0


def test_builtin_chat_has_no_tone():
    provider = FakeProvider(chunks=["hi"])
    client, svc = _client(provider)
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, conv["id"], "this is amazing!")
            _turn(ws, conv["id"], "again")
    assert "tone right now" not in provider.calls[1][0].content
    assert svc.conversations.meta(conv["id"]) == {}
