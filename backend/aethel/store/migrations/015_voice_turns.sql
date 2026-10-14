-- One row per call turn: where the time went between the end of the user's speech and the first audio of the reply.
-- The times are seconds after speech_end_at (so speech_end_at itself is 0); a stage that didn't happen is NULL.
CREATE TABLE voice_turns (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  user_message_id TEXT,
  assistant_message_id TEXT,
  speech_end_at REAL,
  transcript_at REAL,
  decision_at REAL,
  first_token_at REAL,
  first_audio_at REAL,
  eot_p REAL,
  waited_ms INTEGER,
  stt TEXT,
  interrupted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX voice_turns_created ON voice_turns (created_at);
