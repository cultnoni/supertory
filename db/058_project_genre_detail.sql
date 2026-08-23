-- Optional genre_detail under main_genre + sub_genre (사극 / 동양로판 / 대체역사).
-- Empty string means "none". Invalid values are coerced to '' in app code.

ALTER TABLE project ADD COLUMN genre_detail TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (58, 'project_genre_detail');
