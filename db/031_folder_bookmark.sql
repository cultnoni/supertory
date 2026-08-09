-- Folder bookmark flag (binder display only; no header bar / no sort effect).

ALTER TABLE folder ADD COLUMN is_bookmarked INTEGER NOT NULL DEFAULT 0
    CHECK (is_bookmarked IN (0, 1));

INSERT INTO schema_migration(version, name) VALUES (31, 'folder_bookmark');
