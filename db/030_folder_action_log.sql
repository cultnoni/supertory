-- U1: undo stack for simple folder patch actions (rename/color/box/pin).
-- Active stack = undone_at IS NULL; purge keeps latest 20 per project.

CREATE TABLE IF NOT EXISTS folder_action_log (
    id            INTEGER PRIMARY KEY,
    project_id    INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    type          TEXT NOT NULL,
    label_ko      TEXT NOT NULL DEFAULT '',
    payload_json  TEXT NOT NULL,
    undone_at     TEXT,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_folder_action_log_stack
    ON folder_action_log(project_id, undone_at, id DESC);

INSERT INTO schema_migration(version, name) VALUES (30, 'folder_action_log');
