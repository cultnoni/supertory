-- Project index dirty queue for deferred merge on next Tory call.

ALTER TABLE project_index ADD COLUMN index_dirty INTEGER NOT NULL DEFAULT 0
    CHECK (index_dirty IN (0, 1));
ALTER TABLE project_index ADD COLUMN pending_scene_ids_json TEXT NOT NULL DEFAULT '[]';

INSERT INTO schema_migration(version, name) VALUES (20, 'project_index_queue');
