-- Nested manuscripts: a scene may belong under another scene (same chapter).
-- sort_order is unique among siblings (same chapter + same parent_scene_id).

ALTER TABLE scene ADD COLUMN parent_scene_id INTEGER
    REFERENCES scene(id) ON DELETE RESTRICT;

DROP INDEX IF EXISTS ux_scene_active_order;
CREATE UNIQUE INDEX ux_scene_active_order
    ON scene(chapter_id, COALESCE(parent_scene_id, 0), sort_order)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_scene_parent
    ON scene(parent_scene_id, sort_order)
    WHERE deleted_at IS NULL;

-- Soft-delete cascades to direct children; chained updates reach deeper levels.
CREATE TRIGGER IF NOT EXISTS scene_soft_delete_children
AFTER UPDATE OF deleted_at ON scene
WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
BEGIN
    UPDATE scene
    SET deleted_at = NEW.deleted_at,
        updated_at = NEW.deleted_at
    WHERE parent_scene_id = NEW.id AND deleted_at IS NULL;
END;

-- Block restoring a child while its parent manuscript is still trashed.
CREATE TRIGGER IF NOT EXISTS scene_restore_parent_scene_check
BEFORE UPDATE OF deleted_at ON scene
WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL
     AND NEW.parent_scene_id IS NOT NULL
     AND EXISTS (
         SELECT 1 FROM scene
         WHERE id = NEW.parent_scene_id AND deleted_at IS NOT NULL
     )
BEGIN
    SELECT RAISE(ABORT, 'cannot restore a scene under a deleted parent scene');
END;

INSERT INTO schema_migration(version, name) VALUES (16, 'scene_parent');
