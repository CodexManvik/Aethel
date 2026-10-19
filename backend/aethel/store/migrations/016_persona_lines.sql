-- A persona's imported messages (style import, spec §6.2): indexed apart from Aethel's own conversations and recalled
-- only as style examples. The vector is the source of truth; personas/<id>/lines.tvim is a cache.
CREATE TABLE persona_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,   -- the turbovec id
  persona_id TEXT NOT NULL,
  text TEXT NOT NULL,
  ts TEXT,
  vector BLOB NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_persona_lines_persona ON persona_lines(persona_id);

-- Per-conversation state that isn't worth a column each (the tone engine's mood and energy).
ALTER TABLE conversations ADD COLUMN meta TEXT NOT NULL DEFAULT '{}';
