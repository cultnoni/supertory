-- Optional link: which success-pattern profile this project uses as 흥행공식 참고.
ALTER TABLE project ADD COLUMN linked_success_profile_id INTEGER;

INSERT INTO schema_migration(version, name) VALUES (26, 'linked_success_profile');
