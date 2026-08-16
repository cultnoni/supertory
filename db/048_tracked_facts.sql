-- Structured tracked facts for project auto-index (body / inventory / relations / foreshadow).

ALTER TABLE scene_summary ADD COLUMN tracked_facts_json TEXT;
ALTER TABLE project_index ADD COLUMN tracked_facts_json TEXT;

INSERT INTO schema_migration(version, name) VALUES (48, 'tracked_facts');
