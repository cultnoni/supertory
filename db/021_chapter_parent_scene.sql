-- Folders (chapters) may nest under a manuscript (scene).
ALTER TABLE chapter ADD COLUMN parent_scene_id INTEGER
    REFERENCES scene(id) ON DELETE RESTRICT;

-- Top-level and under-scene folders need separate sort_order namespaces.
DROP INDEX IF EXISTS ux_chapter_active_order;
CREATE UNIQUE INDEX ux_chapter_active_order
    ON chapter(
        project_id,
        COALESCE(part_id, 0),
        COALESCE(parent_scene_id, 0),
        sort_order
    )
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_chapter_parent_scene
    ON chapter(parent_scene_id, sort_order)
    WHERE deleted_at IS NULL AND parent_scene_id IS NOT NULL;

-- Soft-delete nested folders when the parent manuscript is trashed
-- (chapter_soft_delete_scenes then cascades to that folder's scenes).
CREATE TRIGGER IF NOT EXISTS chapter_soft_delete_under_scene
AFTER UPDATE OF deleted_at ON scene
WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
BEGIN
    UPDATE chapter
    SET deleted_at = NEW.deleted_at,
        updated_at = NEW.deleted_at
    WHERE parent_scene_id = NEW.id AND deleted_at IS NULL;
END;

INSERT INTO schema_migration(version, name) VALUES (21, 'chapter_parent_scene');
