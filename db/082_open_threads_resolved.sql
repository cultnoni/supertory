-- open_threads_json items become {text, resolved} objects (rewrite in apply_migration_082).

INSERT INTO schema_migration(version, name) VALUES (82, 'open_threads_resolved');
