-- 회차 「이전 버전」의 체크포인트 표시.
-- scene_revision 행은 트리거(scene_revision_immutable_content)로 수정할 수 없으므로
-- (원고 이력을 바꾸지 않는다는 안전장치), 체크포인트는 이 표에 따로 기록한다.
-- reason: manual | before_restore | before_ai_insert | before_replace_all
--         | before_complete | before_overwrite
-- 표시는 지울 수 있다(원고 이력 자체는 그대로 남는다).

CREATE TABLE IF NOT EXISTS scene_revision_mark (
    id           INTEGER PRIMARY KEY,
    scene_id     INTEGER NOT NULL REFERENCES scene(id) ON DELETE CASCADE,
    revision_no  INTEGER NOT NULL CHECK (revision_no > 0),
    reason       TEXT    NOT NULL DEFAULT 'manual',
    label        TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (scene_id, revision_no, reason)
);

CREATE INDEX IF NOT EXISTS ix_scene_revision_mark_scene
    ON scene_revision_mark(scene_id, revision_no);

INSERT INTO schema_migration(version, name)
SELECT 107, 'scene_revision_marks'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 107);
