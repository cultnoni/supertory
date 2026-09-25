-- 장편: 가벼운 「이 장이 한 일」 캐시 (중간 점검·앞 장 컨텍스트용).

CREATE TABLE IF NOT EXISTS literary_unit_work (
    project_id   INTEGER NOT NULL,
    unit_kind    TEXT NOT NULL,
    unit_id      INTEGER NOT NULL,
    source_hash  TEXT NOT NULL DEFAULT '',
    work_json    TEXT NOT NULL DEFAULT '{}',
    one_line     TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (project_id, unit_kind, unit_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_literary_unit_work_hash
    ON literary_unit_work(project_id, source_hash);

INSERT INTO schema_migration(version, name)
SELECT 104, 'literary_unit_work'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 104);
