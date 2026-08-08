-- Manuscript bait tags (떡밥 던지기) — was browser localStorage, now SQLite.

CREATE TABLE IF NOT EXISTS bait (
    id                  TEXT PRIMARY KEY,
    project_id          INTEGER NOT NULL,
    kind                TEXT NOT NULL DEFAULT 'plant'
                        CHECK (kind IN ('plant', 'idea')),
    quote               TEXT NOT NULL DEFAULT '',
    summary             TEXT NOT NULL DEFAULT '',
    recover_content     TEXT NOT NULL DEFAULT '',
    recover_at          TEXT NOT NULL DEFAULT '',
    recover_scene_id    INTEGER,
    plant_scene_id      INTEGER,
    source_scene_id     INTEGER,
    plant_at_note       TEXT NOT NULL DEFAULT '',
    source_title        TEXT NOT NULL DEFAULT '',
    notify_on_recover   INTEGER NOT NULL DEFAULT 1,
    snooze_until        TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (recover_scene_id) REFERENCES scene(id) ON DELETE SET NULL,
    FOREIGN KEY (plant_scene_id) REFERENCES scene(id) ON DELETE SET NULL,
    FOREIGN KEY (source_scene_id) REFERENCES scene(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_bait_project
    ON bait(project_id, created_at DESC, id);

CREATE INDEX IF NOT EXISTS ix_bait_recover_scene
    ON bait(project_id, recover_scene_id);

INSERT INTO schema_migration(version, name) VALUES (23, 'bait');
