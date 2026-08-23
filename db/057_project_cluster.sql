-- Cluster id for 4-cluster genre picker + feature gating.
-- Empty string means "infer from purpose + main/sub genre" (legacy rows).

ALTER TABLE project ADD COLUMN cluster_id TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name) VALUES (57, 'project_cluster');
