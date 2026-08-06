-- Main genre + sub genre for each work (header category pickers).

ALTER TABLE project ADD COLUMN main_genre TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN sub_genre TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (10, 'project_genre');
