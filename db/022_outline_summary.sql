-- Plot outline for submission / contest synopsis (optional author brief).

ALTER TABLE project ADD COLUMN outline_summary TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (22, 'outline_summary');
