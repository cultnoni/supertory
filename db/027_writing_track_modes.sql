-- Writing log: independent auto/manual modes for chars vs time.
-- Defaults: chars auto (1), time manual (0 = only while "기록" is on).

ALTER TABLE writing_prefs ADD COLUMN chars_auto INTEGER NOT NULL DEFAULT 1
    CHECK (chars_auto IN (0, 1));
ALTER TABLE writing_prefs ADD COLUMN time_auto INTEGER NOT NULL DEFAULT 0
    CHECK (time_auto IN (0, 1));

INSERT INTO schema_migration(version, name) VALUES (27, 'writing_track_modes');
