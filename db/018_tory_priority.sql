-- Author priority instructions for Tory (optional; highest weight when set).

ALTER TABLE project ADD COLUMN tory_priority_md TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (18, 'tory_priority');
