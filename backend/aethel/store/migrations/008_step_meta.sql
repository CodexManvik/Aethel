-- What a step touched (element, app), who decided it (agent | macro) and its screen thumbnail (spec §4.5, §6.3).
ALTER TABLE steps ADD COLUMN meta TEXT;
ALTER TABLE steps ADD COLUMN decider TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE steps ADD COLUMN thumbnail TEXT;
