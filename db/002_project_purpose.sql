-- Add work purpose / book category to project.
-- Applied automatically by app.initialise_database() when version 2 is missing.

ALTER TABLE project ADD COLUMN purpose TEXT NOT NULL DEFAULT 'novel';

INSERT INTO schema_migration(version, name) VALUES (2, 'project_purpose');
