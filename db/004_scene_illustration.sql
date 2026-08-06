-- Illustrations for fairy-tale (and any scene that needs picture pages).

CREATE TABLE IF NOT EXISTS scene_illustration (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL,
    scene_id        INTEGER NOT NULL,
    file_name       TEXT NOT NULL,
    mime_type       TEXT NOT NULL DEFAULT 'image/jpeg',
    caption_md      TEXT NOT NULL DEFAULT '',
    overlays_json   TEXT NOT NULL DEFAULT '[]',
    sort_order      INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, scene_id),
    FOREIGN KEY (scene_id, project_id) REFERENCES scene(id, project_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_scene_illustration_scene
    ON scene_illustration(scene_id, sort_order, id);

INSERT INTO schema_migration(version, name) VALUES (4, 'scene_illustration');
