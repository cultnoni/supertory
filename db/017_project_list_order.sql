-- Project list: recent-open order (default) and optional manual list_sort_order.

ALTER TABLE project ADD COLUMN last_opened_at TEXT;
ALTER TABLE project ADD COLUMN list_sort_order INTEGER NOT NULL DEFAULT 0;

ALTER TABLE writing_prefs ADD COLUMN project_list_mode TEXT NOT NULL DEFAULT 'recent'
    CHECK (project_list_mode IN ('recent', 'manual'));

-- Existing works: treat last content update as last opened so recent sort is useful immediately.
UPDATE project
SET last_opened_at = COALESCE(updated_at, created_at)
WHERE last_opened_at IS NULL;

-- Stable initial manual order (id) for when the user switches to manual mode later.
UPDATE project
SET list_sort_order = id
WHERE list_sort_order = 0;

INSERT INTO schema_migration(version, name) VALUES (17, 'project_list_order');
