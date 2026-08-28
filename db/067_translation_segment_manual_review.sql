-- Flag segments whose paragraph translation fell back to the source text.
ALTER TABLE translation_segments ADD COLUMN needs_manual_review INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_migration(version, name)
VALUES (67, 'translation_segment_manual_review');
