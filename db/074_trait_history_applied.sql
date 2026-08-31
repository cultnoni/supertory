-- Whether a detection filled an empty field (1) or only raised a pending badge (0).
-- SQLite cannot ADD COLUMN IF NOT EXISTS; initialise_database also calls
-- ensure_trait_history_applied_column() so this stays idempotent on odd DBs.
-- Backfill of existing rows is done in apply_migration_074() (needs later columns).

ALTER TABLE character_trait_history ADD COLUMN applied INTEGER NOT NULL DEFAULT 0;
ALTER TABLE item_trait_history ADD COLUMN applied INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_migration(version, name) VALUES (74, 'trait_history_applied');
