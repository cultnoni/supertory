-- Optional content_rating for romance playbook intensity overlay.
-- Empty string means unset (no section). Invalid values are rejected in app code.

ALTER TABLE project ADD COLUMN content_rating TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (65, 'project_content_rating');
