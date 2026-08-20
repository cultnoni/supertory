-- Completed-episode virtual-reader comments (auto-generated, read-only).

CREATE TABLE IF NOT EXISTS scene_reader_comments (
    id              INTEGER PRIMARY KEY,
    scene_id        INTEGER NOT NULL,
    persona_id      TEXT NOT NULL,
    comment_text    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (scene_id, persona_id),
    FOREIGN KEY (scene_id) REFERENCES scene(id) ON DELETE CASCADE,
    FOREIGN KEY (persona_id) REFERENCES virtual_reader_personas(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_scene_reader_comments_scene
    ON scene_reader_comments(scene_id, created_at, id);

INSERT INTO schema_migration(version, name) VALUES (54, 'scene_reader_comments');
