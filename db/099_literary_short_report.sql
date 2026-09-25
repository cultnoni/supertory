-- Literary short report: pipeline tag, scene paragraph offset, contest settings.
-- pipeline stays empty for existing web-novel runs. run_kind is unchanged.

ALTER TABLE feedback_run ADD COLUMN pipeline TEXT NOT NULL DEFAULT '';
ALTER TABLE feedback_run_scene ADD COLUMN para_offset INTEGER NOT NULL DEFAULT 0;

ALTER TABLE project ADD COLUMN contest_prep INTEGER NOT NULL DEFAULT 0;
ALTER TABLE project ADD COLUMN contest_name TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN contest_pages_min INTEGER;
ALTER TABLE project ADD COLUMN contest_pages_max INTEGER;

INSERT INTO schema_migration(version, name)
SELECT 99, 'literary_short_report'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 99);
