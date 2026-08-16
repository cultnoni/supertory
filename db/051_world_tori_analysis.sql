-- Pending Tory worldbuilding-sheet analysis for fields the author already filled.

CREATE TABLE IF NOT EXISTS world_tori_analysis (
    id                INTEGER PRIMARY KEY,
    project_id        INTEGER NOT NULL,
    section_name      TEXT NOT NULL DEFAULT '',
    field_name        TEXT NOT NULL CHECK (length(trim(field_name)) > 0),
    analyzed_content  TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (project_id, field_name),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_world_tori_analysis_project
    ON world_tori_analysis(project_id);

INSERT INTO schema_migration(version, name) VALUES (51, 'world_tori_analysis');
