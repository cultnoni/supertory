-- External Scrivener-style project package handle (.stg file).

ALTER TABLE project ADD COLUMN uuid TEXT;
ALTER TABLE project ADD COLUMN package_path TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_project_uuid
    ON project(uuid) WHERE uuid IS NOT NULL AND deleted_at IS NULL;

INSERT INTO schema_migration(version, name) VALUES (3, 'project_package');
