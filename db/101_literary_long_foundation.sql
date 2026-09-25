-- 장편 분석 단위의 마지막 장, 요약 캐시 해시, 모티프를 열어 둔 표시.

ALTER TABLE project ADD COLUMN literary_finale_kind TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN literary_finale_id INTEGER NOT NULL DEFAULT 0;

ALTER TABLE scene_summary ADD COLUMN content_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE scene_summary ADD COLUMN stale INTEGER NOT NULL DEFAULT 0;

ALTER TABLE bait ADD COLUMN intentionally_open INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_migration(version, name)
SELECT 101, 'literary_long_foundation'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 101);
