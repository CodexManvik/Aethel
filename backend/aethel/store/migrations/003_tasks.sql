CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  goal TEXT NOT NULL,
  state TEXT NOT NULL,
  plan TEXT NOT NULL DEFAULT '[]',
  plan_done TEXT NOT NULL DEFAULT '[]',
  checks TEXT NOT NULL DEFAULT '[]',
  summary TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_tasks_conversation ON tasks(conversation_id);

CREATE TABLE steps (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  tool TEXT NOT NULL,
  args TEXT NOT NULL,
  summary TEXT NOT NULL,
  verdict TEXT NOT NULL,
  ok INTEGER,
  result TEXT,
  duration_ms INTEGER,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_steps_task ON steps(task_id, idx);
