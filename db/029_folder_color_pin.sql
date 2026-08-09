-- Folder accent color + pin-to-top among siblings (no pin_order column).
-- Display order: is_pinned DESC, sort_order ASC, id ASC. sort_order unchanged on pin.

ALTER TABLE folder ADD COLUMN color TEXT
    CHECK (
        color IS NULL OR color IN (
            'red', 'orange', 'yellow', 'green', 'blue', 'purple', 'gray'
        )
    );

ALTER TABLE folder ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0
    CHECK (is_pinned IN (0, 1));

INSERT INTO schema_migration(version, name) VALUES (29, 'folder_color_pin');
