-- Allow trait history without a scene (settings inherit from a previous work).
-- Rebuilds character_trait_history and item_trait_history; existing rows keep scene_id.

CREATE TABLE character_trait_history_077 (
    id                INTEGER PRIMARY KEY,
    character_id      INTEGER NOT NULL,
    project_id        INTEGER NOT NULL,
    scene_id          INTEGER,
    field_name        TEXT NOT NULL CHECK (length(trim(field_name)) > 0),
    detected_content  TEXT NOT NULL DEFAULT '',
    applied           INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (scene_id) REFERENCES scene(id) ON DELETE CASCADE
);
INSERT INTO character_trait_history_077
    (id, character_id, project_id, scene_id, field_name, detected_content, applied, created_at)
SELECT id, character_id, project_id, scene_id, field_name, detected_content,
       COALESCE(applied, 0), created_at
FROM character_trait_history;
DROP TABLE character_trait_history;
ALTER TABLE character_trait_history_077 RENAME TO character_trait_history;
CREATE INDEX IF NOT EXISTS ix_character_trait_history_character
    ON character_trait_history(character_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_character_trait_history_scene
    ON character_trait_history(scene_id, id);

CREATE TABLE item_trait_history_077 (
    id                INTEGER PRIMARY KEY,
    item_id           INTEGER NOT NULL,
    project_id        INTEGER NOT NULL,
    scene_id          INTEGER,
    field_name        TEXT NOT NULL CHECK (length(trim(field_name)) > 0),
    detected_content  TEXT NOT NULL DEFAULT '',
    applied           INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (item_id) REFERENCES item(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (scene_id) REFERENCES scene(id) ON DELETE CASCADE
);
INSERT INTO item_trait_history_077
    (id, item_id, project_id, scene_id, field_name, detected_content, applied, created_at)
SELECT id, item_id, project_id, scene_id, field_name, detected_content,
       COALESCE(applied, 0), created_at
FROM item_trait_history;
DROP TABLE item_trait_history;
ALTER TABLE item_trait_history_077 RENAME TO item_trait_history;
CREATE INDEX IF NOT EXISTS ix_item_trait_history_item
    ON item_trait_history(item_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_item_trait_history_scene
    ON item_trait_history(scene_id, id);

INSERT INTO schema_migration(version, name) VALUES (77, 'trait_history_scene_nullable');
