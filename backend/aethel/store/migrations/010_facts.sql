CREATE TABLE facts (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,                 -- 'user' | 'persona:<persona_id>'
  text TEXT NOT NULL,
  vector BLOB NOT NULL,                -- embedder float32, L2-normalised
  source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_facts_scope ON facts(scope);

CREATE TABLE fact_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fact_id TEXT NOT NULL,               -- kept after the fact is deleted
  op TEXT NOT NULL CHECK (op IN ('add', 'update', 'delete')),
  old_text TEXT,
  new_text TEXT,
  actor TEXT NOT NULL CHECK (actor IN ('extractor', 'user')),
  source_message_id TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_fact_events_fact ON fact_events(fact_id);
