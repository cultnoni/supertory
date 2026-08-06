-- Reference links attached to each scene (research, notes, sources).

ALTER TABLE scene ADD COLUMN reference_links_json TEXT NOT NULL DEFAULT '[]';

INSERT INTO schema_migration(version, name) VALUES (5, 'scene_reference_links');
