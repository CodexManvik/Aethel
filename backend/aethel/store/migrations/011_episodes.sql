CREATE TABLE episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,   -- the turbovec id
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_message_id TEXT NOT NULL,
  assistant_message_id TEXT NOT NULL UNIQUE,
  text TEXT NOT NULL,
  vector BLOB NOT NULL,                   -- float32, L2-normalised: the source of truth; the .tvim file is a cache
  created_at TEXT NOT NULL
);
CREATE INDEX idx_episodes_conversation ON episodes(conversation_id);
