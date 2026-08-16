-- Last-used document-import scene delimiter settings (JSON).

ALTER TABLE project ADD COLUMN import_delimiter_config TEXT;

INSERT INTO schema_migration(version, name) VALUES (49, 'import_delimiter_config');
