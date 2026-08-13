-- Idea notes: pin to binder footer (목차 하단 메모 고정).

ALTER TABLE idea_note ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_migration(version, name) VALUES (033, 'idea_note_pin');
