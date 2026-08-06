-- Character strengths (무기/강점) and weaknesses for character settings.

ALTER TABLE character ADD COLUMN strengths_md TEXT NOT NULL DEFAULT '';
ALTER TABLE character ADD COLUMN weaknesses_md TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (15, 'character_strengths_weaknesses');
