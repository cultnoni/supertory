-- Per-project flag: first-time "scene marked complete" guide card.

ALTER TABLE project ADD COLUMN completion_guide_shown INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_migration(version, name) VALUES (79, 'project_completion_guide');
