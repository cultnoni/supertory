-- Character canvas groups: named clusters of 3+ characters on the relation board.

CREATE TABLE IF NOT EXISTS character_canvas_group (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL,
    name        TEXT NOT NULL CHECK (length(trim(name)) > 0 AND length(trim(name)) <= 40),
    color       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_character_canvas_group_project
    ON character_canvas_group(project_id);

CREATE TABLE IF NOT EXISTS character_canvas_group_member (
    group_id     INTEGER NOT NULL,
    project_id   INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    PRIMARY KEY (group_id, character_id),
    UNIQUE (project_id, character_id),
    FOREIGN KEY (group_id) REFERENCES character_canvas_group(id) ON DELETE CASCADE,
    FOREIGN KEY (character_id, project_id) REFERENCES character(id, project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_character_canvas_group_member_project
    ON character_canvas_group_member(project_id);

INSERT INTO schema_migration(version, name) VALUES (87, 'character_canvas_groups');
