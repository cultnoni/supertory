-- Allow multiple virtual-reader comment batches per scene.
-- Existing rows for a scene become one batch keyed by that scene's earliest created_at.

CREATE TABLE scene_reader_comments_080 (
    id              INTEGER PRIMARY KEY,
    scene_id        INTEGER NOT NULL,
    batch_id        TEXT NOT NULL,
    persona_id      TEXT NOT NULL,
    comment_text    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (scene_id, batch_id, persona_id),
    FOREIGN KEY (scene_id) REFERENCES scene(id) ON DELETE CASCADE,
    FOREIGN KEY (persona_id) REFERENCES virtual_reader_personas(id) ON DELETE RESTRICT
);

INSERT INTO scene_reader_comments_080
    (id, scene_id, batch_id, persona_id, comment_text, created_at)
SELECT
    c.id,
    c.scene_id,
    COALESCE(
        NULLIF(trim((
            SELECT MIN(c2.created_at)
            FROM scene_reader_comments c2
            WHERE c2.scene_id = c.scene_id
        )), ''),
        'legacy-' || CAST(c.scene_id AS TEXT)
    ),
    c.persona_id,
    c.comment_text,
    c.created_at
FROM scene_reader_comments c;

DROP TABLE scene_reader_comments;
ALTER TABLE scene_reader_comments_080 RENAME TO scene_reader_comments;

CREATE INDEX IF NOT EXISTS ix_scene_reader_comments_scene
    ON scene_reader_comments(scene_id, batch_id, created_at, id);

INSERT INTO schema_migration(version, name) VALUES (80, 'scene_reader_comment_batches');
