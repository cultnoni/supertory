-- Pending Tory character-sheet analysis for fields the author already filled.

CREATE TABLE IF NOT EXISTS character_tori_analysis (
    id                INTEGER PRIMARY KEY,
    character_id      INTEGER NOT NULL,
    field_name        TEXT NOT NULL CHECK (length(trim(field_name)) > 0),
    analyzed_content  TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (character_id, field_name),
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_character_tori_analysis_character
    ON character_tori_analysis(character_id);

INSERT INTO schema_migration(version, name) VALUES (50, 'character_tori_analysis');
