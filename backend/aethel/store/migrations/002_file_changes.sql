CREATE TABLE file_changes (
  id TEXT PRIMARY KEY,
  task_id TEXT,
  path TEXT NOT NULL,
  existed INTEGER NOT NULL,
  before BLOB,
  created_at TEXT NOT NULL,
  rolled_back INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_file_changes_task ON file_changes(task_id);
