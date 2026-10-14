import asyncio
import json
import struct

import pytest

from aethel.services import build_services
from aethel.voice.session import VoiceSession
from aethel.voice.stt import SpeechToTextError, Transcript
from aethel.voice.tts import Speech
from tests.fakes import FakeLocal, FakeProvider, factory_from

pytestmark = pytest.mark.anyio

NL = chr(10)
SENTENCES = [f"This is the number {n} sentence of the reply. " for n in range(5)]


class FakeSTT:
    name = "fake:stt"

    def __init__(self, fail=None):
        self.fail = fail

    def transcribe(self, pcm16, sample_rate=16000):
        if self.fail:
            raise self.fail
        return Transcript(pcm16.decode(), "en", 1.0, 1)


class FakeTTS:
    prefer_azure = False

    class kokoro:  # noqa: N801
        @staticmethod
        def warm():
            pass

    def __init__(self, delay=0.0):
        self.calls, self.delay = [], delay

    async def synthesize(self, text, blend, private):
        self.calls.append((text, private))
        if self.delay:
            await asyncio.sleep(self.delay)
        return Speech(text.encode(), "pcm16", 24000, 0.1)


class FakeEOT:
    """P(finished) for what was said: decided by a function, and every question is recorded."""

    def __init__(self, p=lambda text: 0.95):
        self.p, self.asked = p, []

    async def finished(self, utterance, previous_reply):
        self.asked.append((utterance, previous_reply))
        return self.p(utterance)


class SlowProvider(FakeProvider):
    """Streams one chunk at a time and counts how many were pulled, so a cancelled stream is visible."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.pulled = 0

    async def stream(self, messages, **kw):
        async for ev in super().stream(messages, **kw):
            self.pulled += 1
            yield ev


class Call:
    """A VoiceSession wired to lists instead of a socket."""

    def __init__(self, provider=None, *, tts=None, eot=None, stt=None, ack_delay_s=30.0, settings=None, hello=True):
        self.provider = provider or FakeProvider(chunks=SENTENCES[:2])
        self.svc = build_services(provider_factory=factory_from({"groq:g": self.provider}),
                                  local_llm=FakeLocal(fail="none"))
        self.svc.keys.set_many({"groq": "k"})
        self.svc.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]},
                                  "voice": {"short_wait_ms": 0, "max_wait_ms": 250, "ack": True}, **(settings or {})})
        self.tts, self.eot, self.stt = tts or FakeTTS(), eot or FakeEOT(), stt or FakeSTT()
        self.svc.voice.stt, self.svc.voice.tts, self.svc.voice.eot = (lambda: self.stt), (lambda: self.tts), (lambda: self.eot)
        self.conv = self.svc.conversations.create()
        self.json, self.audio = [], []
        self.session = VoiceSession(self.svc, self._send, self._send_bytes, ack_delay_s=ack_delay_s)
        self._want_hello = hello

    async def _send(self, payload):
        self.json.append(json.loads(payload))

    async def _send_bytes(self, payload):
        self.audio.append(payload)
        self.json.append({"type": "_audio", "sentence_id": struct.unpack("<i", payload[:4])[0], "body": payload[4:]})

    async def __aenter__(self):
        if self._want_hello:
            await self.hello()
        return self

    async def __aexit__(self, *exc):
        await self.session.close()
        await self.svc.chat.wait_idle()

    async def hello(self, conversation_id=None):
        await self.session.on_text(json.dumps({"type": "hello", "conversation_id": conversation_id or self.conv.id}))

    async def say(self, text, end=True):
        await self.session.on_text('{"type": "speech_start"}')
        await self.session.on_text('{"type": "speech_end", "duration_ms": 1000}')
        await self.session.on_audio(text.encode())

    async def interrupt(self, last, turn_id=None):
        await self.session.on_text(json.dumps({"type": "interrupt", "last_started_sentence_id": last,
                                               **({"turn_id": turn_id} if turn_id else {})}))

    def types(self):
        return [m["type"] for m in self.json]

    def of(self, type_):
        return [m for m in self.json if m["type"] == type_]

    async def until(self, predicate, timeout=5.0):
        async def poll():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(poll(), timeout)


async def test_happy_path_in_order_and_saved_as_voice_messages():
    async with Call() as call:
        assert call.of("ready") == [{"type": "ready", "stt": "fake:stt", "tts_voice": "af_heart",
                                     "persona_name": "Aethel"}]
        await call.say("what time does it open")
        await call.until(lambda: "turn_end" in call.types())
        types = call.types()
        assert types[:2] == ["ready", "caption"]
        assert call.of("caption")[0] == {"type": "caption", "role": "user", "text": "what time does it open",
                                         "final": True, "message_id": None}
        assert types.index("thinking") < types.index("tts_meta")
        metas = call.of("tts_meta")
        assert [m["sentence_id"] for m in metas] == [0, 1] and metas[0]["codec"] == "pcm16"
        assert metas[0]["text"] == SENTENCES[0].strip() and metas[0]["sample_rate"] == 24000
        # each tts_meta is followed by its audio frame: 4-byte id + the audio
        for meta in metas:
            i = call.json.index(meta)
            assert call.json[i + 1]["type"] == "_audio" and call.json[i + 1]["sentence_id"] == meta["sentence_id"]
            assert call.json[i + 1]["body"] == meta["text"].encode()
        end = call.of("turn_end")[0]
        assert end["status"] == "complete" and end["turn_id"] == metas[0]["turn_id"]
        final = [c for c in call.of("caption") if c["role"] == "assistant"][0]
        assert final["text"] == "".join(SENTENCES[:2]) and final["final"] and final["message_id"]
        msgs = call.svc.messages.list(call.conv.id)
        assert [(m.role, m.content, m.meta.get("modality")) for m in msgs] == [
            ("user", "what time does it open", "voice"), ("assistant", "".join(SENTENCES[:2]), "voice")]
        # the next turn knows what was said last
        assert call.session.last_reply == "".join(SENTENCES[:2])


async def test_barge_in_cuts_the_saved_reply_to_what_was_heard():
    provider = SlowProvider(chunks=SENTENCES, delay=0.3)
    async with Call(provider, ack_delay_s=5) as call:
        await call.say("tell me a story")
        await call.until(lambda: len(call.of("tts_meta")) >= 2)   # sentences 0 and 1 have been sent
        await call.interrupt(1)
        await call.until(lambda: "turn_end" in call.types())
        await call.svc.chat.wait_idle()
        assistant = [m for m in call.svc.messages.list(call.conv.id) if m.role == "assistant"][0]
        assert assistant.content == " ".join(s.strip() for s in SENTENCES[:2])
        assert assistant.status == "stopped" and assistant.meta["interrupted"] is True
        assert provider.pulled < len(SENTENCES) + 1   # the model stopped being asked for more
        sent_before = len(call.of("tts_meta"))
        await asyncio.sleep(0.8)
        assert len(call.of("tts_meta")) == sent_before   # nothing after the interrupt
        assert call.of("turn_end")[0]["status"] == "interrupted"
        # what the user heard is what Aethel remembers saying
        assert call.session.last_reply == assistant.content
        assert call.svc.db.query_one("SELECT interrupted FROM voice_turns")["interrupted"] == 1


async def test_barge_in_before_any_sentence_started_saves_nothing_said():
    provider = SlowProvider(chunks=SENTENCES, delay=0.3)
    async with Call(provider, ack_delay_s=5) as call:
        await call.say("tell me a story")
        await call.until(lambda: "thinking" in call.types())
        await call.interrupt(None)
        await call.until(lambda: "turn_end" in call.types())
        await call.svc.chat.wait_idle()
        assistant = [m for m in call.svc.messages.list(call.conv.id) if m.role == "assistant"][0]
        assert assistant.content == "" and assistant.status == "stopped"


async def test_a_pause_mid_thought_is_one_turn_with_both_parts():
    eot = FakeEOT(lambda text: 0.9 if text.endswith("Sundays") else 0.2)
    async with Call(eot=eot) as call:
        await call.say("I need a library")
        await asyncio.sleep(0.12)          # a pause shorter than the long silence
        await call.say("that opens on Sundays")
        await call.until(lambda: "turn_end" in call.types())
        users = [m for m in call.svc.messages.list(call.conv.id) if m.role == "user"]
        assert [m.content for m in users] == ["I need a library that opens on Sundays"]
        assert [q for q, _ in eot.asked] == ["I need a library", "I need a library that opens on Sundays"]
        assert len(call.of("turn_end")) == 1


async def test_the_acknowledgement_plays_when_the_first_words_are_slow_and_not_when_they_are_fast():
    slow = FakeProvider(chunks=SENTENCES[:1], delay=0.8)
    async with Call(slow, ack_delay_s=0.2) as call:
        await call.until(lambda: len(call.session._acks) == 3)
        await call.say("hello there")
        await call.until(lambda: "turn_end" in call.types())
        acks = [m for m in call.of("tts_meta") if m["sentence_id"] == -1]
        assert len(acks) == 1 and acks[0]["text"] in ("Mm.", "Okay—", "Right.")
        audio = [m for m in call.of("_audio") if m["sentence_id"] == -1]
        assert audio and audio[0]["body"] == acks[0]["text"].encode()
        # it isn't part of the transcript
        assistant = [m for m in call.svc.messages.list(call.conv.id) if m.role == "assistant"][0]
        assert acks[0]["text"] not in assistant.content

    async with Call(FakeProvider(chunks=SENTENCES[:1]), ack_delay_s=0.5) as call:
        await call.until(lambda: len(call.session._acks) == 3)
        await call.say("hello there")
        await call.until(lambda: "turn_end" in call.types())
        await asyncio.sleep(0.6)
        assert [m for m in call.of("tts_meta") if m["sentence_id"] == -1] == []


async def test_no_acknowledgement_when_it_is_turned_off():
    async with Call(FakeProvider(chunks=SENTENCES[:1], delay=0.5), ack_delay_s=0.1,
                    settings={"voice": {"ack": False, "short_wait_ms": 0, "max_wait_ms": 250}}) as call:
        await call.say("hello there")
        await call.until(lambda: "turn_end" in call.types())
        assert [m for m in call.of("tts_meta") if m["sentence_id"] == -1] == []


async def test_a_task_request_starts_a_task_and_says_on_it(monkeypatch):
    async def task_route(*args):
        return "task"

    monkeypatch.setattr("aethel.voice.session.route", task_route)
    async with Call() as call:
        started = []

        async def start(**kw):
            started.append(kw)

        call.svc.engine.start = start
        await call.say("open notepad")
        await call.until(lambda: "turn_end" in call.types())
        assert started == [{"conversation_id": call.conv.id, "goal": "open notepad", "meta": {"modality": "voice"}}]
        assert [m["text"] for m in call.of("tts_meta")] == ["On it."]
        assert call.provider.calls == []     # no chat turn


async def test_a_stop_request_cancels_the_running_tasks_and_says_stopped(monkeypatch):
    async def stop_route(*args):
        return "stop_task"

    monkeypatch.setattr("aethel.voice.session.route", stop_route)
    async with Call() as call:
        task = call.svc.tasks.create(call.conv.id, "organise my files")
        cancelled = []

        async def cancel(task_id):
            cancelled.append(task_id)

        call.svc.engine.cancel = cancel
        await call.say("stop that")
        await call.until(lambda: "turn_end" in call.types())
        assert cancelled == [task.id]
        assert [m["text"] for m in call.of("tts_meta")] == ["Stopped."]
        assert [(m.role, m.content) for m in call.svc.messages.list(call.conv.id)] == [("user", "stop that"),
                                                                                       ("assistant", "Stopped.")]


async def test_private_mode_is_passed_to_the_voice_so_it_stays_local():
    tts = FakeTTS()
    async with Call(tts=tts, settings={"private_mode": True}) as call:
        await call.say("hello there")
        await call.until(lambda: "turn_end" in call.types())
        assert tts.calls and all(private is True for _, private in tts.calls)


async def test_a_second_call_on_the_same_conversation_is_refused():
    async with Call() as first:
        second = Call(hello=False)
        second.svc = first.svc
        second.session = VoiceSession(first.svc, second._send, second._send_bytes)
        await second.hello(first.conv.id)
        assert second.of("voice_error") == [{"type": "voice_error", "message": "This conversation is already in a call.",
                                             "fatal": True}]
        assert second.session.ended
        await second.session.close()
        assert first.conv.id in first.svc.voice.calls          # the refused one didn't free the first's line
    assert first.conv.id not in first.svc.voice.calls           # ending the call frees it


async def test_hello_for_a_missing_conversation_is_fatal():
    async with Call(hello=False) as call:
        await call.hello("conv_nope")
        assert call.of("voice_error")[0]["fatal"] is True and call.session.ended


async def test_messages_before_hello_are_refused():
    async with Call(hello=False) as call:
        await call.session.on_text('{"type": "speech_start"}')
        await call.session.on_audio(b"abc")
        assert [e["fatal"] for e in call.of("voice_error")] == [False, False]
        await call.session.on_text("not json")
        assert "wasn't understood" in call.of("voice_error")[-1]["message"]


async def test_speech_recognition_failing_is_reported_and_the_call_goes_on():
    async with Call(stt=FakeSTT(fail=SpeechToTextError("Speech recognition isn't available: no key."))) as call:
        await call.say("hello")
        await call.until(lambda: call.of("voice_error"))
        assert call.of("voice_error")[0] == {"type": "voice_error", "fatal": False,
                                             "message": "Speech recognition isn't available: no key."}
        call.stt.fail = None
        await call.say("hello again")
        await call.until(lambda: "turn_end" in call.types())


async def test_a_misfire_ends_the_speaking_without_a_turn():
    async with Call() as call:
        await call.session.on_text('{"type": "speech_start"}')
        await call.session.on_text('{"type": "speech_end", "duration_ms": 80, "misfire": true}')
        await asyncio.sleep(0.4)
        assert "thinking" not in call.types()
        await call.say("hello there")
        await call.until(lambda: "turn_end" in call.types())


async def test_a_chat_error_is_spoken_and_ends_the_turn_as_an_error():
    from aethel.providers.base import ProviderError
    async with Call(FakeProvider(error=ProviderError("boom", retryable=False))) as call:
        await call.say("hello there")
        await call.until(lambda: "turn_end" in call.types())
        assert call.of("turn_end")[0]["status"] == "error"
        assert [m["text"] for m in call.of("tts_meta")] == ["Sorry, I couldn't answer that just now."]


async def test_the_timing_row_is_ordered():
    async with Call() as call:
        await call.say("what time does it open")
        await call.until(lambda: "turn_end" in call.types())
        row = call.svc.db.query_one("SELECT * FROM voice_turns")
        assert row["conversation_id"] == call.conv.id and row["stt"] == "fake:stt" and row["interrupted"] == 0
        assert row["user_message_id"] and row["assistant_message_id"] and row["eot_p"] == 0.95
        order = [row[k] for k in ("speech_end_at", "transcript_at", "decision_at", "first_token_at", "first_audio_at")]
        assert order[0] == 0.0 and order == sorted(order) and all(t is not None for t in order)
        assert call.svc.voice.last_turn_ms() == int(row["first_audio_at"] * 1000)


async def test_end_call_stops_a_reply_in_flight():
    provider = SlowProvider(chunks=SENTENCES, delay=0.3)
    async with Call(provider, ack_delay_s=5) as call:
        await call.say("tell me a story")
        await call.until(lambda: len(call.of("tts_meta")) >= 1)
        await call.session.on_text('{"type": "end_call"}')
        assert call.session.ended
        await call.session.close()
        await call.svc.chat.wait_idle()
        assistant = [m for m in call.svc.messages.list(call.conv.id) if m.role == "assistant"][0]
        assert assistant.status == "stopped"
        assert call.conv.id not in call.svc.voice.calls


async def test_an_interrupt_that_arrives_after_the_server_finished_still_cuts_the_saved_reply():
    # Speech synthesis is faster than playback: the turn is long over while the client is still playing sentence 0.
    async with Call(FakeProvider(chunks=SENTENCES[:3])) as call:
        await call.say("tell me three things")
        await call.until(lambda: "turn_end" in call.types())
        assistant = [m for m in call.svc.messages.list(call.conv.id) if m.role == "assistant"][0]
        assert assistant.status == "complete" and assistant.content == "".join(SENTENCES[:3])
        turn_id = call.of("turn_end")[0]["turn_id"]
        await call.interrupt(0, turn_id)
        cut = call.svc.messages.get(assistant.id)
        assert (cut.content, cut.status, cut.meta["interrupted"]) == (SENTENCES[0].strip(), "stopped", True)
        assert call.session.last_reply == SENTENCES[0].strip()
        assert call.svc.db.query_one("SELECT interrupted FROM voice_turns")["interrupted"] == 1
        # the client's caption is corrected to what was heard
        assert call.of("caption")[-1] == {"type": "caption", "role": "assistant", "text": SENTENCES[0].strip(),
                                          "final": True, "message_id": assistant.id}
        await call.interrupt(0, turn_id)   # a repeat changes nothing
        assert call.svc.messages.get(assistant.id).content == SENTENCES[0].strip()


async def test_an_interrupt_naming_another_turn_does_not_cut_this_one():
    async with Call(FakeProvider(chunks=SENTENCES[:3])) as call:
        await call.say("tell me three things")
        await call.until(lambda: "turn_end" in call.types())
        await call.interrupt(0, "vt_some_other_turn")
        assistant = [m for m in call.svc.messages.list(call.conv.id) if m.role == "assistant"][0]
        assert assistant.status == "complete" and assistant.content == "".join(SENTENCES[:3])


async def test_speaking_while_the_last_words_are_still_being_recognised_holds_the_answer_back():
    class SlowSTT(FakeSTT):
        def transcribe(self, pcm16, sample_rate=16000):
            import time
            time.sleep(0.6)
            return super().transcribe(pcm16, sample_rate)

    async with Call(stt=SlowSTT()) as call:
        await call.session.on_text('{"type": "speech_start"}')
        await call.session.on_text('{"type": "speech_end", "duration_ms": 900}')
        await call.session.on_audio(b"I need a library")
        await asyncio.sleep(0.1)
        await call.session.on_text('{"type": "speech_start"}')   # the user carries on while STT 1 is running
        await asyncio.sleep(1.2)                                  # STT 1 is back, and its wait has long run out
        assert "thinking" not in call.types()                     # ...but they are still speaking
        await call.session.on_text('{"type": "speech_end", "duration_ms": 900}')
        await call.session.on_audio(b"that opens on Sundays")
        await call.until(lambda: "turn_end" in call.types(), 8)
        users = [m.content for m in call.svc.messages.list(call.conv.id) if m.role == "user"]
        assert users == ["I need a library that opens on Sundays"]


async def test_switching_private_mode_on_mid_call_moves_speech_recognition_off_azure():
    first, local = FakeSTT(), FakeSTT()
    first.name, local.name = "azure:uksouth", "local:faster-whisper-small"
    async with Call(stt=first) as call:
        engines = iter([local])
        call.svc.voice.stt = lambda: next(engines)
        call.svc.settings.update({"private_mode": True})
        await call.say("hello there")
        await call.until(lambda: "turn_end" in call.types())
        assert call.session.stt is local
        assert call.svc.db.query_one("SELECT stt FROM voice_turns")["stt"] == "local:faster-whisper-small"


async def test_code_in_a_reply_is_shown_but_not_read_aloud():
    reply = ["Here is how it works.", NL, "```python", NL, "x = compute_total(items)", NL, "print(x)", NL, "```", NL,
             "That prints the total for you."]
    tts = FakeTTS()
    async with Call(FakeProvider(chunks=reply), tts=tts) as call:
        await call.say("show me")
        await call.until(lambda: "turn_end" in call.types())
        assert [t for t, _ in tts.calls if t not in ("Mm.", "Okay—", "Right.")] == [
            "Here is how it works.", "That prints the total for you."]
        assistant = [m for m in call.svc.messages.list(call.conv.id) if m.role == "assistant"][0]
        assert "compute_total" in assistant.content    # the saved reply keeps the code


async def test_a_failed_turn_tells_the_client_and_stops_what_it_started():
    async with Call() as call:
        async def broken(*args, **kwargs):
            raise RuntimeError("the chat service fell over")

        call.svc.chat.run_turn = broken
        await call.say("hello there")
        await call.until(lambda: "turn_end" in call.types())
        assert call.of("turn_end")[0]["status"] == "error"
        assert any("went wrong" in e["message"] for e in call.of("voice_error"))
        await asyncio.sleep(0.05)
        assert call.session._turn is None
        left = [t.get_coro().__qualname__ for t in asyncio.all_tasks() if not t.done()]
        assert not [n for n in left if "_speak" in n or "_ack_later" in n]   # no worker left waiting forever


async def test_the_conversation_is_freed_even_if_stopping_the_reply_fails():
    provider = SlowProvider(chunks=SENTENCES, delay=0.3)
    call = Call(provider, ack_delay_s=30)
    await call.__aenter__()
    await call.say("tell me a story")
    await call.until(lambda: len(call.of("tts_meta")) >= 1)

    async def boom(message_id):
        raise RuntimeError("stop failed")

    call.svc.chat.stop = boom
    with pytest.raises(RuntimeError):
        await call.session.close()
    assert call.conv.id not in call.svc.voice.calls
