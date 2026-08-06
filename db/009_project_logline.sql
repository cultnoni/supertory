-- Project logline (one-line pitch). Lives between synopsis and worldbuilding in the UI.

ALTER TABLE project ADD COLUMN logline_md TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (9, 'project_logline');
