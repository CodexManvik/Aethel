"""One call: the user speaks, Aethel decides when they've finished, answers sentence by sentence in its own voice,
and stops the moment they speak over it.

  utterance (PCM) → speech to text → caption → turn manager (waits until it's Aethel's turn) → intent routing →
  chat turn (tokens stream into the sentence chunker) → text to speech per sentence → audio to the client

Barge-in: the client stops playback and says which sentence had started; the reply is cut to exactly the sentences
that were heard."""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import anyio
from pydantic import ValidationError

from ..chat.router import route
from ..protocol import UserMessage
from ..runtime.store import TERMINAL_STATES
from ..store.repos import new_id, now_iso
from .chunker import SentenceChunker
from .protocol import (MAX_UTTERANCE_BYTES, Caption, EndCall, Hello, Interrupt, Ready, SpeechEnd, SpeechStart,
                       Thinking, TtsMeta, TurnEnd, VoiceError, client_adapter)
from .stt import SpeechToTextError
from .tts import VoiceBlend, speakable
from .turns import TurnManager

log = logging.getLogger("aethel.voice")
ACK_DELAY_S = 0.6                       # no first words by then: say "Mm." (settings.voice.ack)
ACKS = ("Mm.", "Okay—", "Right.")
MAX_PENDING_UTTERANCES = 4
ON_IT, STOPPED, SORRY = "On it.", "Stopped.", "Sorry, I couldn't answer that just now."

Send = Callable[[str], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]


@dataclass
class _Turn:
    id: str
    speech_end_at: float
    decision_at: float
    eot_p: float | None
    waited_ms: int
    chunker: SentenceChunker = field(default_factory=SentenceChunker)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    texts: list[str] = field(default_factory=list)     # the sentences sent as speech, in order: id = index
    transcript_at: float | None = None
    first_token_at: float | None = None
    first_audio_at: float | None = None
    user_id: str | None = None
    assistant_id: str | None = None
    worker: asyncio.Task | None = None
    ack: asyncio.Task | None = None
    interrupted: bool = False
    heard_upto: int | None = None                      # the last sentence the user heard begin
    spoke_error: bool = False


class VoiceSession:
    def __init__(self, svc, send: Send, send_bytes: SendBytes, *, clock: Callable[[], float] = time.monotonic,
                 ack_delay_s: float = ACK_DELAY_S):
        self.svc, self._send, self._send_bytes, self.clock, self.ack_delay_s = svc, send, send_bytes, clock, ack_delay_s
        self.conversation_id: str | None = None
        self.ended = False
        self.last_reply: str | None = None
        self.stt = self.tts = self.turns = None
        self.blend: VoiceBlend | None = None
        self._turn: _Turn | None = None
        self._loop: asyncio.Task | None = None
        self._utterances: set[asyncio.Task] = set()
        self._stt_lock = asyncio.Lock()
        self._speech_end_at: float | None = None
        self._timing: tuple[float, float] | None = None  # (speech ended, transcript ready) of the latest words
        self._acks: dict[str, object] = {}
        self._ack_next = 0
        self._ack_prep: asyncio.Task | None = None
        self._holds_call = False

    # ---- messages in -------------------------------------------------------------------------------------
    async def on_text(self, raw: str) -> None:
        try:
            msg = client_adapter.validate_json(raw)
        except ValidationError:
            await self._error("That message wasn't understood.")
            return
        if isinstance(msg, Hello):
            await self._hello(msg)
        elif self.turns is None:
            await self._error("Say hello first: the call hasn't started.")
        elif isinstance(msg, SpeechStart):
            self.turns.on_speech_start()
        elif isinstance(msg, SpeechEnd):
            self._speech_end_at = self.clock()
            if msg.misfire:
                await self.turns.on_utterance("", self.last_reply)  # a noise: nothing was said, nothing to wait for
        elif isinstance(msg, Interrupt):
            await self._interrupt(msg.last_started_sentence_id)
        elif isinstance(msg, EndCall):
            self.ended = True

    async def on_audio(self, pcm: bytes) -> None:
        if self.turns is None:
            await self._error("Say hello first: the call hasn't started.")
            return
        if not pcm or len(pcm) > MAX_UTTERANCE_BYTES:
            await self._error("That audio was empty or too long.")
            await self.turns.on_utterance("", self.last_reply)
            return
        if len(self._utterances) >= MAX_PENDING_UTTERANCES:
            await self._error("Too much audio at once; I dropped some.")
            return
        end_at, self._speech_end_at = self._speech_end_at or self.clock(), None
        task = asyncio.ensure_future(self._utterance(pcm, end_at))
        self._utterances.add(task)
        task.add_done_callback(self._utterances.discard)

    # ---- the call ----------------------------------------------------------------------------------------
    async def _hello(self, msg: Hello) -> None:
        if self.turns is not None:
            await self._error("The call has already started.")
            return
        conv = self.svc.conversations.get(msg.conversation_id)
        if conv is None:
            await self._error("That conversation doesn't exist.", fatal=True)
            return
        if conv.id in self.svc.voice.calls:
            await self._error("This conversation is already in a call.", fatal=True)
            return
        self.svc.voice.calls.add(conv.id)
        self._holds_call = True
        self.conversation_id = conv.id
        s = self.svc.settings.get()
        try:
            self.blend = VoiceBlend.parse(s.voice.default_blend)
        except ValueError:
            self.blend = VoiceBlend.parse({"af_heart": 1.0})
        self.stt, self.tts = self.svc.voice.stt(), self.svc.voice.tts()
        try:
            await anyio.to_thread.run_sync(self._warm)
        except Exception as exc:
            log.warning("warming the voice engines failed: %r", exc)
        v = s.voice
        self.turns = TurnManager(self.svc.voice.eot(), threshold=v.eot_threshold, short_wait_ms=v.short_wait_ms,
                                 max_wait_ms=v.max_wait_ms, clock=self.clock)
        self._loop = asyncio.ensure_future(self._turn_loop())
        if v.ack:
            self._ack_prep = asyncio.ensure_future(self._prepare_acks())
        voice = v.azure_voice if getattr(self.tts, "prefer_azure", False) else self.blend.weights[0][0]
        persona = "Aethel" if conv.persona_id == "aethel" else conv.persona_id.replace("_", " ").title()
        await self._push(Ready(stt=self.stt.name, tts_voice=voice, persona_name=persona))

    def _warm(self) -> None:
        if hasattr(self.stt, "warm"):
            self.stt.warm()
        if not getattr(self.tts, "prefer_azure", False):
            self.tts.kokoro.warm()

    async def close(self) -> None:
        """The call is over (end_call, or the socket dropped): stop everything it started."""
        self.ended = True
        turn = self._turn
        if turn is not None:
            turn.interrupted = True
            if turn.assistant_id:
                await self.svc.chat.stop(turn.assistant_id)
        tasks = [t for t in [self._loop, self._ack_prep, *self._utterances] if t is not None]
        if turn is not None:
            tasks += [t for t in (turn.worker, turn.ack) if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._holds_call:
            self.svc.voice.calls.discard(self.conversation_id)
            self._holds_call = False

    # ---- listening ---------------------------------------------------------------------------------------
    async def _utterance(self, pcm: bytes, end_at: float) -> None:
        async with self._stt_lock:
            try:
                heard = await anyio.to_thread.run_sync(self.stt.transcribe, pcm)
            except SpeechToTextError as exc:
                await self._error(str(exc))
                await self.turns.on_utterance("", self.last_reply)
                return
            except Exception:
                log.exception("speech recognition failed")
                await self._error("Speech recognition failed.")
                await self.turns.on_utterance("", self.last_reply)
                return
            text = heard.text.strip()
            if text:
                self._timing = (end_at, self.clock())
                await self._push(Caption(role="user", text=text, final=True))
            await self.turns.on_utterance(text, self.last_reply, silence_since=end_at)

    async def _turn_loop(self) -> None:
        while not self.ended:
            decision = await self.turns.next_turn()
            end_at, transcript_at = self._timing or (self.clock(), self.clock())
            try:
                await self._take_turn(decision, end_at, transcript_at)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("a call turn failed")
                self._turn = None
                await self._error("Something went wrong with that turn.")

    # ---- one turn ----------------------------------------------------------------------------------------
    async def _take_turn(self, decision, end_at: float, transcript_at: float) -> None:
        svc, cid = self.svc, self.conversation_id
        turn = self._turn = _Turn(new_id("vt"), end_at, self.clock(), decision.eot_p, decision.waited_ms,
                                  transcript_at=transcript_at)
        await self._push(Thinking())
        running = [t for t in svc.tasks.list_for_conversation(cid) if t.state not in TERMINAL_STATES]
        kind = await route(svc.system1, svc.settings, decision.text, running[-1].goal if running else None)
        meta = {"modality": "voice"}
        status = "complete"
        if kind == "task":
            await svc.engine.start(conversation_id=cid, goal=decision.text, meta=meta)
            await self._say(turn, ON_IT)
        elif kind == "stop_task":
            for t in running:
                await svc.engine.cancel(t.id)
            turn.user_id = svc.messages.add(cid, "user", decision.text, meta=meta).id
            turn.assistant_id = svc.messages.add(cid, "assistant", STOPPED, meta=meta).id
            await self._say(turn, STOPPED)
        else:
            status = await self._chat(turn, decision.text, meta)
        if turn.interrupted:
            status = "interrupted"
        await self._push(TurnEnd(turn_id=turn.id, status=status))
        self._record(turn, status)
        self._turn = None

    async def _chat(self, turn: _Turn, text: str, meta: dict) -> str:
        svc = self.svc

        def started(user_id: str, assistant_id: str) -> None:
            turn.user_id, turn.assistant_id = user_id, assistant_id
            if turn.interrupted:  # spoken over before the reply even existed
                asyncio.ensure_future(svc.chat.stop(assistant_id))

        async def sink(delta: str) -> None:
            if turn.first_token_at is None:
                turn.first_token_at = self.clock()
                if turn.ack is not None:
                    turn.ack.cancel()
            if not turn.interrupted:
                for sentence in turn.chunker.feed(delta):
                    turn.queue.put_nowait(sentence)

        turn.worker = asyncio.ensure_future(self._speak(turn))
        if svc.settings.get().voice.ack and self._acks:
            turn.ack = asyncio.ensure_future(self._ack_later(turn))
        # Its own task: stopping the reply (barge-in) cancels this task, not the call's turn loop.
        chat_turn = asyncio.ensure_future(svc.chat.run_turn(
            UserMessage(conversation_id=self.conversation_id, text=text), sink=sink, meta=meta, started=started))
        result = await chat_turn
        if turn.ack is not None:
            turn.ack.cancel()
        if not turn.interrupted:
            for sentence in turn.chunker.flush():
                turn.queue.put_nowait(sentence)
        turn.queue.put_nowait(None)
        await asyncio.gather(turn.worker, return_exceptions=True)
        if result.status == "error" and not turn.interrupted:
            await self._say(turn, SORRY)
        saved = svc.messages.get(result.assistant_message_id) if result.assistant_message_id else None
        content = saved.content if saved is not None else ""
        if turn.interrupted and saved is not None:
            heard = -1 if turn.heard_upto is None else turn.heard_upto
            content = " ".join(turn.texts[: max(-1, heard) + 1])  # exactly what was heard
            svc.messages.update(saved.id, content=content, status="stopped", meta={**saved.meta, "interrupted": True})
        if content:
            await self._push(Caption(role="assistant", text=content, final=True, message_id=result.assistant_message_id))
        self.last_reply = content or self.last_reply
        return result.status

    async def _speak(self, turn: _Turn) -> None:
        """Text to speech for the reply's sentences, in order, while the model is still writing the rest."""
        while True:
            sentence = await turn.queue.get()
            if sentence is None:
                return
            if turn.interrupted:
                continue
            await self._speak_sentence(turn, sentence)

    async def _speak_sentence(self, turn: _Turn, sentence: str) -> None:
        spoken = speakable(sentence)
        if not spoken:
            return
        speech = await self.tts.synthesize(spoken, self.blend, private=self.svc.settings.get().private_mode)
        if turn.interrupted:
            return
        if speech is None:
            if not turn.spoke_error:
                turn.spoke_error = True
                await self._error("I can't speak right now: no voice is available.")
            return
        sentence_id = len(turn.texts)
        turn.texts.append(sentence)
        await self._push(TtsMeta(sentence_id=sentence_id, text=sentence, sample_rate=speech.sample_rate, turn_id=turn.id))
        await self._send_bytes(sentence_id.to_bytes(4, "little", signed=True) + speech.audio)
        if turn.first_audio_at is None:
            turn.first_audio_at = self.clock()

    async def _say(self, turn: _Turn, text: str) -> None:
        """A fixed line spoken outside the model ("On it.")."""
        await self._speak_sentence(turn, text)
        await self._push(Caption(role="assistant", text=text, final=True))

    # ---- the short acknowledgement -----------------------------------------------------------------------
    async def _prepare_acks(self) -> None:
        for text in ACKS:
            try:
                speech = await self.tts.synthesize(text, self.blend, private=self.svc.settings.get().private_mode)
            except Exception:
                log.exception("preparing an acknowledgement failed")
                return
            if speech is not None:
                self._acks[text] = speech

    async def _ack_later(self, turn: _Turn) -> None:
        await asyncio.sleep(self.ack_delay_s)
        if turn.first_token_at is not None or turn.interrupted or not self._acks:
            return
        text = list(self._acks)[self._ack_next % len(self._acks)]
        self._ack_next += 1
        speech = self._acks[text]
        await self._push(TtsMeta(sentence_id=-1, text=text, sample_rate=speech.sample_rate, turn_id=turn.id))
        await self._send_bytes((-1).to_bytes(4, "little", signed=True) + speech.audio)

    # ---- barge-in ----------------------------------------------------------------------------------------
    async def _interrupt(self, last_started: int | None) -> None:
        turn = self._turn
        if turn is None or turn.interrupted:
            return
        turn.interrupted = True
        turn.heard_upto = last_started
        for task in (turn.ack, turn.worker):
            if task is not None:
                task.cancel()
        while not turn.queue.empty():
            turn.queue.get_nowait()
        if turn.assistant_id:
            await self.svc.chat.stop(turn.assistant_id)

    # ---- bookkeeping -------------------------------------------------------------------------------------
    def _record(self, turn: _Turn, status: str) -> None:
        def rel(t: float | None) -> float | None:
            return None if t is None else round(max(0.0, t - turn.speech_end_at), 3)

        try:
            self.svc.db.execute(
                "INSERT INTO voice_turns (id, conversation_id, user_message_id, assistant_message_id, speech_end_at,"
                " transcript_at, decision_at, first_token_at, first_audio_at, eot_p, waited_ms, stt, interrupted,"
                " created_at) VALUES (?, ?, ?, ?, 0.0, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (turn.id, self.conversation_id, turn.user_id, turn.assistant_id, rel(turn.transcript_at),
                 rel(turn.decision_at), rel(turn.first_token_at), rel(turn.first_audio_at), turn.eot_p,
                 turn.waited_ms, self.stt.name, int(turn.interrupted), now_iso()))
        except Exception:
            log.exception("recording the turn's timing failed")

    async def _push(self, msg) -> None:
        await self._send(msg.model_dump_json())

    async def _error(self, message: str, fatal: bool = False) -> None:
        self.ended = self.ended or fatal
        await self._push(VoiceError(message=message, fatal=fatal))
