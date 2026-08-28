-- Persist the chapter range a translation job was created with.
ALTER TABLE translation_jobs ADD COLUMN start_chapter INTEGER;
ALTER TABLE translation_jobs ADD COLUMN end_chapter INTEGER;
ALTER TABLE translation_jobs ADD COLUMN translate_all_chapters INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_migration(version, name)
VALUES (68, 'translation_job_chapter_range');
