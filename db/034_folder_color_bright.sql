-- Bright/vivid folder color variant (second palette row), independent of `color`.
-- `color` (red/orange/yellow/green/blue/purple/gray) is untouched — this only adds
-- a new column so both the muted and vivid palettes can be stored side by side.
-- Display priority (screen-side, not enforced here): color_bright wins when set.

ALTER TABLE folder ADD COLUMN color_bright TEXT
    CHECK (
        color_bright IS NULL OR color_bright IN (
            'black',
            'bright_red', 'bright_orange', 'bright_yellow', 'bright_green',
            'bright_blue', 'bright_purple', 'bright_gray'
        )
    );

INSERT INTO schema_migration(version, name) VALUES (34, 'folder_color_bright');
