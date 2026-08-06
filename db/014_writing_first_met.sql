-- SuperTORY install / "first met Tory" day for calendar badge.

ALTER TABLE writing_prefs ADD COLUMN first_met_day TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (14, 'writing_first_met');
