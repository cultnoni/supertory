-- Character relationship canvas: saved card positions and directed-undirected
-- relation edges (AI suggestions + author-confirmed / manual lines).

CREATE TABLE IF NOT EXISTS character_canvas_position (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL,
    character_id    INTEGER NOT NULL,
    x               REAL NOT NULL DEFAULT 0,
    y               REAL NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (project_id, character_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (character_id, project_id) REFERENCES character(id, project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_character_canvas_position_project
    ON character_canvas_position(project_id);

CREATE TABLE IF NOT EXISTS character_relations (
    id                  INTEGER PRIMARY KEY,
    project_id          INTEGER NOT NULL,
    character_a_id      INTEGER NOT NULL,
    character_b_id      INTEGER NOT NULL,
    label               TEXT NOT NULL CHECK (length(trim(label)) > 0),
    status              TEXT NOT NULL CHECK (status IN ('suggested', 'confirmed')),
    source              TEXT NOT NULL CHECK (source IN ('ai', 'manual')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (character_a_id < character_b_id),
    UNIQUE (project_id, character_a_id, character_b_id, label),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (character_a_id, project_id) REFERENCES character(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (character_b_id, project_id) REFERENCES character(id, project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_character_relations_project
    ON character_relations(project_id, status);

INSERT INTO schema_migration(version, name) VALUES (75, 'character_relations');
