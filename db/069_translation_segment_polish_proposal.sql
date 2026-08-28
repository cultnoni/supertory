-- Store AI chapter-polish proposals separately from the chosen final polish_text.
ALTER TABLE translation_segments ADD COLUMN polish_proposal_text TEXT;
ALTER TABLE translation_segments ADD COLUMN polish_choice TEXT;

INSERT INTO schema_migration(version, name)
VALUES (69, 'translation_segment_polish_proposal');
