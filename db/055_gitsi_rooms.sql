-- Gitsi (Jitsi) coworking rooms: create/join history.

CREATE TABLE IF NOT EXISTS gitsi_rooms (
    id INTEGER PRIMARY KEY,
    room_code TEXT UNIQUE NOT NULL,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_gitsi_rooms_active
    ON gitsi_rooms(is_active, created_at, id);

INSERT INTO schema_migration(version, name) VALUES (55, 'gitsi_rooms');
