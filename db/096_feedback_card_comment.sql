-- 첨삭 카드 한 장에 대한 사용자·토리 의견 대화.
-- 카드/실행이 지워지면 댓글도 CASCADE.

CREATE TABLE IF NOT EXISTS feedback_card_comment (
    id          INTEGER PRIMARY KEY,
    card_id     INTEGER NOT NULL,
    run_id      INTEGER NOT NULL,
    project_id  INTEGER NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    body        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, project_id),
    FOREIGN KEY (card_id) REFERENCES feedback_card(id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, project_id) REFERENCES feedback_run(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_feedback_card_comment_card_created
    ON feedback_card_comment(card_id, created_at);

INSERT INTO schema_migration(version, name)
SELECT 96, 'feedback_card_comment'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 96);
