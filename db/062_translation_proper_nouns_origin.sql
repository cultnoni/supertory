-- Track whether a proper noun came from the character/world index or AI detection.
-- source: 'character_index' | 'ai_detected'

ALTER TABLE translation_proper_nouns ADD COLUMN source TEXT NOT NULL DEFAULT 'ai_detected';

INSERT INTO schema_migration(version, name) VALUES (62, 'translation_proper_nouns_source');
