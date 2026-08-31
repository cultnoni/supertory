-- Allow multiple labels between the same pair (연인 and 주인 at once).
-- Rebuilds character_relations; existing rows including confirmed are copied as-is.

CREATE TABLE character_relations_076 (
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

INSERT INTO character_relations_076
    (id, project_id, character_a_id, character_b_id, label, status, source, created_at)
SELECT id, project_id, character_a_id, character_b_id, label, status, source, created_at
FROM character_relations;

DROP TABLE character_relations;
ALTER TABLE character_relations_076 RENAME TO character_relations;
CREATE INDEX IF NOT EXISTS ix_character_relations_project
    ON character_relations(project_id, status);

INSERT INTO schema_migration(version, name) VALUES (76, 'character_relations_label_unique');
