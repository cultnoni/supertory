-- Literary track length + six independent style fields.
-- literary_form: NULL | 'short' | 'long'. NULL for every work that is not
-- 일반문학/순문학, and for older 일반문학/순문학 rows until the writer chooses.
-- Style columns are updated one field at a time (no composed document).

ALTER TABLE project ADD COLUMN literary_form TEXT;
ALTER TABLE project ADD COLUMN style_narration TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN style_sentence TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN style_dialogue TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN style_lexicon TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN style_choice TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN style_habit TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (97, 'project_literary_form');
