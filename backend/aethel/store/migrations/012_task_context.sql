ALTER TABLE tasks ADD COLUMN context TEXT NOT NULL DEFAULT '{}';  -- remembered facts shown to the planner
