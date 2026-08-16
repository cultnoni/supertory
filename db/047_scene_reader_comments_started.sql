-- Per-scene flag: virtual-reader comments flow has been started at least once.

ALTER TABLE scene ADD COLUMN reader_comments_started INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_migration(version, name) VALUES (47, 'scene_reader_comments_started');
