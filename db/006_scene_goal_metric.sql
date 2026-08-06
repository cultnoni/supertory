-- Which unit the scene writing goal uses (chars with/without spaces, words, letters).

ALTER TABLE scene ADD COLUMN goal_metric TEXT NOT NULL DEFAULT 'chars_no_space';

INSERT INTO schema_migration(version, name) VALUES (6, 'scene_goal_metric');
