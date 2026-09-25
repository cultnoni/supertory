-- 장편: 이전 단위 요약을 5개씩 묶은 압축 캐시.

CREATE TABLE IF NOT EXISTS literary_summary_bundle (
    project_id   INTEGER NOT NULL,
    start_ord    INTEGER NOT NULL,
    end_ord      INTEGER NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    summary_md   TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (project_id, start_ord, end_ord),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_literary_summary_bundle_project
    ON literary_summary_bundle(project_id, start_ord, end_ord);

INSERT INTO schema_migration(version, name)
SELECT 102, 'literary_summary_bundle'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 102);
