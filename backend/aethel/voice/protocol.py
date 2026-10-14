"""The /ws/voice protocol (parent spec §11). JSON messages are typed by `type`; audio travels as binary frames:

  client → server: after `speech_end`, one binary frame with the utterance as 16 kHz mono PCM16 (little-endian).
  server → client: after each `tts_meta`, one binary frame: a 4-byte little-endian sentence id, then the audio.

The frontend's types are generated from the schema: py -3.11 scripts/gen_event_schema.py && (cd frontend_app && pnpm gen:types)"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ..paths import PROJECT_ROOT

VOICE_SCHEMA_PATH = PROJECT_ROOT / "frontend_app" / "src" / "lib" / "voice.schema.json"
SAMPLE_RATE = 16000
MAX_UTTERANCE_BYTES = 60 * SAMPLE_RATE * 2  # a minute: Azure's short-audio limit, and a sane bound for any engine


class Msg(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


# ---- client → server ------------------------------------------------------
class Hello(Msg):
    type: Literal["hello"] = "hello"
    conversation_id: str


class SpeechStart(Msg):
    type: Literal["speech_start"] = "speech_start"


class SpeechEnd(Msg):
    """The user stopped speaking. The utterance's audio follows as a binary frame, unless `misfire`: the voice
    detector fired on a noise and there is nothing to transcribe."""
    type: Literal["speech_end"] = "speech_end"
    duration_ms: int = Field(ge=0, le=60_000)
    misfire: bool = False


class Interrupt(Msg):
    """The user spoke over the reply: playback has stopped. `last_started_sentence_id` is the last sentence that began
    to play (None: none had), which is what the saved reply is cut to."""
    type: Literal["interrupt"] = "interrupt"
    last_started_sentence_id: int | None = None
    turn_id: str | None = None  # the turn whose audio was playing (from its tts_meta): a late interrupt can't hit another


class EndCall(Msg):
    type: Literal["end_call"] = "end_call"


# ---- server → client ------------------------------------------------------
class Ready(Msg):
    type: Literal["ready"] = "ready"
    stt: str
    tts_voice: str
    persona_name: str


class Caption(Msg):
    type: Literal["caption"] = "caption"
    role: Literal["user", "assistant"]
    text: str
    final: bool
    message_id: str | None = None


class Thinking(Msg):
    """Aethel has decided it's its turn; the first audio follows."""
    type: Literal["thinking"] = "thinking"


class TtsMeta(Msg):
    type: Literal["tts_meta"] = "tts_meta"
    sentence_id: int  # the reply's sentences count from 0; a short acknowledgement ("Mm.") is -1 and isn't saved
    text: str
    codec: Literal["pcm16"] = "pcm16"
    sample_rate: int
    turn_id: str


class TurnEnd(Msg):
    type: Literal["turn_end"] = "turn_end"
    turn_id: str
    status: Literal["complete", "interrupted", "stopped", "error"]


class VoiceError(Msg):
    type: Literal["voice_error"] = "voice_error"
    message: str
    fatal: bool


ClientMessage = Annotated[Union[Hello, SpeechStart, SpeechEnd, Interrupt, EndCall], Field(discriminator="type")]
ServerMessage = Annotated[Union[Ready, Caption, Thinking, TtsMeta, TurnEnd, VoiceError], Field(discriminator="type")]

client_adapter: TypeAdapter = TypeAdapter(ClientMessage)


class VoiceProtocol(BaseModel):
    server: ServerMessage
    client: ClientMessage


def export_voice_schema() -> dict:
    return VoiceProtocol.model_json_schema(mode="serialization")
