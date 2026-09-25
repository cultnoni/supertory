-- Literary conflict pendings: link to conflict_id and allow reclaim without delete.

ALTER TABLE character_tori_analysis ADD COLUMN conflict_id TEXT NOT NULL DEFAULT '';
ALTER TABLE character_tori_analysis ADD COLUMN status TEXT NOT NULL DEFAULT 'pending';

ALTER TABLE world_tori_analysis ADD COLUMN conflict_id TEXT NOT NULL DEFAULT '';
ALTER TABLE world_tori_analysis ADD COLUMN status TEXT NOT NULL DEFAULT 'pending';

CREATE INDEX IF NOT EXISTS ix_character_tori_analysis_conflict
    ON character_tori_analysis(conflict_id) WHERE conflict_id != '';
CREATE INDEX IF NOT EXISTS ix_world_tori_analysis_conflict
    ON world_tori_analysis(conflict_id) WHERE conflict_id != '';

INSERT INTO schema_migration(version, name) VALUES (103, 'literary_pending_reclaim');
