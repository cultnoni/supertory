-- Project-level idea bank sticky notes.

CREATE TABLE IF NOT EXISTS idea_note (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    body_md         TEXT NOT NULL DEFAULT '',
    color           TEXT NOT NULL DEFAULT 'yellow'
                    CHECK (color IN ('yellow', 'pink', 'blue', 'green', 'orange', 'purple')),
    sort_order      INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, project_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_idea_note_project
    ON idea_note(project_id, sort_order, id);

INSERT INTO schema_migration(version, name) VALUES (7, 'idea_bank');
