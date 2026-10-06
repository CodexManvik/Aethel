-- Per-conversation web switch: NULL follows Settings, 1 is on, 0 is off.
ALTER TABLE conversations ADD COLUMN web INTEGER;
