"""What calls share: the Kokoro model and the local Whisper model (each loaded once), which conversations are in a
call, and how the last turn went."""
from .stt import LocalWhisper, SpeechToText, make_stt
from .tts import TTS, KokoroTTS, make_tts
from .turns import EndOfTurn


class VoiceService:
    def __init__(self, *, settings, keys, system1, db):
        self.settings, self.keys, self.system1, self.db = settings, keys, system1, db
        self.kokoro = KokoroTTS()
        self._local: dict[str, LocalWhisper] = {}
        self.calls: set[str] = set()  # conversation ids that are in a call right now

    # Each call builds its engines from the settings as they are when it starts. Tests replace these.
    def stt(self) -> SpeechToText:
        size = self.settings.get().voice.stt_model
        local = self._local.setdefault(size, LocalWhisper(size=size))
        return make_stt(self.settings, self.keys, local=local)

    def tts(self) -> TTS:
        return make_tts(self.settings, self.keys, self.kokoro)

    def eot(self) -> EndOfTurn:
        return EndOfTurn(self.system1)

    def last_turn_ms(self) -> int | None:
        """Milliseconds from the end of the user's speech to the first audio of the last reply that had one."""
        row = self.db.query_one("SELECT first_audio_at FROM voice_turns WHERE first_audio_at IS NOT NULL"
                                " ORDER BY created_at DESC LIMIT 1")
        return None if row is None else int(row["first_audio_at"] * 1000)
