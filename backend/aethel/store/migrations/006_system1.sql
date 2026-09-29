-- Every System 1 call, for evaluation (spec §5.1, E2).
CREATE TABLE s1_calls (
    id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    state TEXT NOT NULL,
    questions TEXT NOT NULL,
    answers TEXT,
    error TEXT,
    latency_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX s1_calls_purpose ON s1_calls (purpose, created_at);
