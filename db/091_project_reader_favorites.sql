-- Per-project favorite virtual readers (자주쓰는 가상독자 모음).
-- Persona pool is global; favorites are per work so each manuscript can
-- keep a different short list.

CREATE TABLE IF NOT EXISTS project_reader_favorites (
    project_id INTEGER NOT NULL,
    persona_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (project_id, persona_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE,
    FOREIGN KEY (persona_id) REFERENCES virtual_reader_personas(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_project_reader_favorites_project
    ON project_reader_favorites(project_id, sort_order, persona_id);

INSERT INTO schema_migration(version, name) VALUES (91, 'project_reader_favorites');
