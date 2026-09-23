CREATE TABLE conversations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  persona_id TEXT NOT NULL DEFAULT 'aethel',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'complete'
    CHECK (status IN ('complete', 'streaming', 'stopped', 'error')),
  meta TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id);

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
