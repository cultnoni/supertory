-- One Gitsi room can be marked as the default join target.

ALTER TABLE gitsi_rooms ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS ux_gitsi_rooms_one_default
    ON gitsi_rooms(is_default) WHERE is_default = 1;

INSERT INTO schema_migration(version, name) VALUES (56, 'gitsi_room_default');
