-- Project auto-index (phase 1): cumulative project index + per-scene structured summaries.

CREATE TABLE IF NOT EXISTS project_index (
    project_id            INTEGER PRIMARY KEY,
    characters_json       TEXT NOT NULL DEFAULT '[]',
    world_rules_json      TEXT NOT NULL DEFAULT '[]',
    timeline_json         TEXT NOT NULL DEFAULT '[]',
    open_threads_json     TEXT NOT NULL DEFAULT '[]',
    last_synced_scene_id  INTEGER,
    updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (last_synced_scene_id) REFERENCES scene(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS scene_summary (
    scene_id    INTEGER PRIMARY KEY,
    summary     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (scene_id) REFERENCES scene(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_project_index_synced_scene
    ON project_index(last_synced_scene_id);

INSERT INTO schema_migration(version, name) VALUES (19, 'project_index');
