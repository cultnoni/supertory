-- Generic Tory notifications. Pipeline kinds are added later; this table is the store.
-- An open row (unread/read/snoozed) with the same dedupe_key is updated, not duplicated.
-- dismissed_forever blocks any later create for that project + dedupe_key.

CREATE TABLE IF NOT EXISTS tory_notification (
    id                  INTEGER PRIMARY KEY,
    project_id          INTEGER NOT NULL,
    kind                TEXT NOT NULL DEFAULT '',
    title               TEXT NOT NULL DEFAULT '',
    body                TEXT NOT NULL DEFAULT '',
    payload_json        TEXT NOT NULL DEFAULT '{}',
    actions_json        TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'unread'
                        CHECK (status IN ('unread', 'read', 'resolved', 'dismissed', 'snoozed')),
    snooze_until        TEXT,
    resolved_action_id  TEXT NOT NULL DEFAULT '',
    resolved_at         TEXT,
    dedupe_key          TEXT NOT NULL DEFAULT '',
    dismissed_forever   INTEGER NOT NULL DEFAULT 0 CHECK (dismissed_forever IN (0, 1)),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_tory_notification_project_status
    ON tory_notification(project_id, status, id);

CREATE INDEX IF NOT EXISTS ix_tory_notification_dedupe
    ON tory_notification(project_id, dedupe_key);

INSERT INTO schema_migration(version, name)
SELECT 98, 'tory_notification'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 98);
