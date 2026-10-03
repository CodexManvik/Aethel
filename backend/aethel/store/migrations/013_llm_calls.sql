CREATE TABLE llm_calls (
  id TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  purpose TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  task_id TEXT,
  message_id TEXT,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  cached_tokens INTEGER,
  estimated INTEGER NOT NULL DEFAULT 0,  -- 1 when the provider reported no usage (a chars/4 estimate)
  breakdown TEXT NOT NULL DEFAULT '{}',  -- estimated prompt tokens by part: system, tools, history, observations
  status TEXT NOT NULL,                  -- ok | error | cancelled
  latency_ms INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_llm_calls_task ON llm_calls(task_id);
CREATE INDEX idx_llm_calls_created ON llm_calls(created_at);
