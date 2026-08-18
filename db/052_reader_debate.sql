-- Virtual-reader debate panel: multi-persona sessions + round messages.
-- (049–051 already used; debate tables ship as migration 052.)

CREATE TABLE IF NOT EXISTS reader_debate_sessions (
    id                  TEXT PRIMARY KEY,
    work_id             TEXT NOT NULL,
    persona_ids_key     TEXT NOT NULL,
    persona_order_json  TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (work_id, persona_ids_key),
    FOREIGN KEY (work_id) REFERENCES project(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_reader_debate_sessions_work
    ON reader_debate_sessions(work_id, persona_ids_key);

CREATE TABLE IF NOT EXISTS reader_debate_messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    round_number    INTEGER NOT NULL,
    speaker_type    TEXT NOT NULL CHECK (speaker_type IN ('user', 'persona')),
    persona_id      TEXT,
    message         TEXT NOT NULL DEFAULT '',
    turn_order      INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES reader_debate_sessions(id) ON DELETE CASCADE,
    CHECK (
        (speaker_type = 'user' AND persona_id IS NULL)
        OR (speaker_type = 'persona' AND length(trim(persona_id)) > 0)
    )
);

CREATE INDEX IF NOT EXISTS ix_reader_debate_messages_session
    ON reader_debate_messages(session_id, round_number, turn_order);

INSERT INTO schema_migration(version, name) VALUES (52, 'reader_debate');
