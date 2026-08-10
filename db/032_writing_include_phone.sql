-- Include phone-app writing stats in the PC writing log (calendar / totals).

ALTER TABLE writing_prefs ADD COLUMN include_phone_log INTEGER NOT NULL DEFAULT 1
    CHECK (include_phone_log IN (0, 1));

INSERT INTO schema_migration(version, name) VALUES (032, 'writing_include_phone');
