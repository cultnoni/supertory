-- Detected character traits from a completed scene. Always stored, even when
-- the sheet field was already filled (pending badge) and not applied yet.
CREATE TABLE IF NOT EXISTS character_trait_history (
    id                INTEGER PRIMARY KEY,
    character_id      INTEGER NOT NULL,
    project_id        INTEGER NOT NULL,
    scene_id          INTEGER NOT NULL,
    field_name        TEXT NOT NULL CHECK (length(trim(field_name)) > 0),
    detected_content  TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (scene_id) REFERENCES scene(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_character_trait_history_character
    ON character_trait_history(character_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_character_trait_history_scene
    ON character_trait_history(scene_id, id);

INSERT INTO schema_migration(version, name) VALUES (72, 'character_trait_history');
