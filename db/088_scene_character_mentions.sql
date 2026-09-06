-- Manuscript mention index: which registered characters (name + aliases)
-- appear as substrings in a scene body. Used by the "등장 이력" dock widget.
-- Matching reuses scene_cast_detect (same boundary-aware labels as cast-sync).

CREATE TABLE IF NOT EXISTS scene_character_mention (
    scene_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    matched_label TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (scene_id, character_id),
    FOREIGN KEY (scene_id) REFERENCES scene(id) ON DELETE CASCADE,
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_scene_character_mention_character
    ON scene_character_mention(character_id, scene_id);

CREATE INDEX IF NOT EXISTS ix_scene_character_mention_project
    ON scene_character_mention(project_id, character_id);

CREATE TABLE IF NOT EXISTS scene_character_mention_state (
    project_id INTEGER PRIMARY KEY,
    indexed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);

INSERT INTO schema_migration(version, name)
VALUES (88, 'scene_character_mentions');
