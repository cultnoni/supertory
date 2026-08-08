-- Optional portrait image for character sheets (file under data/illustrations).

ALTER TABLE character ADD COLUMN portrait_file TEXT NOT NULL DEFAULT '';
ALTER TABLE character ADD COLUMN portrait_mime TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (24, 'character_portrait');
