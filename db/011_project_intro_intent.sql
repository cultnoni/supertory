-- Work introduction + creative intent (설정집 작품소개/기획의도).

ALTER TABLE project ADD COLUMN intro_md TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN intent_md TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (11, 'project_intro_intent');
