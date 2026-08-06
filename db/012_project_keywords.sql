-- Work keywords / trope tags (설정집 키워드 박스). Stored as JSON array of strings.

ALTER TABLE project ADD COLUMN keywords TEXT NOT NULL DEFAULT '[]';

INSERT INTO schema_migration(version, name) VALUES (12, 'project_keywords');
