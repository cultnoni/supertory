-- Personal glossary for coined words and world terms (토리 사전).
-- Not linked to character/item/world rows; overlap is informational only.

CREATE TABLE IF NOT EXISTS custom_dictionary_terms (
    id           INTEGER PRIMARY KEY,
    project_id   INTEGER NOT NULL,
    term         TEXT NOT NULL CHECK (length(trim(term)) > 0),
    definition   TEXT NOT NULL DEFAULT '',
    memo         TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_custom_dictionary_terms_project
    ON custom_dictionary_terms(project_id, updated_at DESC, id DESC);

INSERT INTO schema_migration(version, name) VALUES (89, 'custom_dictionary_terms');
