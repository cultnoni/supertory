-- Project worldbuilding notes. Synopsis uses existing description_md.

ALTER TABLE project ADD COLUMN worldbuilding_md TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (8, 'project_worldbuilding');
