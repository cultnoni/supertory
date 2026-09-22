-- 첨삭 피드백 실행(run) / 회차 스냅샷 / 카드.
-- 프로젝트는 soft delete이므로 project_id는 ON DELETE RESTRICT (scene_character 등과 동일).
-- 회차 완전 삭제(_hard_delete_scene)가 자식을 먼저 지운 뒤 scene을 지우므로
-- scene_id도 ON DELETE RESTRICT. 실행 삭제 시 카드·스냅샷은 CASCADE.

CREATE TABLE IF NOT EXISTS feedback_run (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL,
    run_kind        TEXT NOT NULL CHECK (run_kind IN ('analyze', 'analyze_multi', 'legacy')),
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'ok', 'partial', 'failed')),
    model           TEXT NOT NULL DEFAULT '',
    prompt_version  TEXT NOT NULL DEFAULT '',
    params_json     TEXT NOT NULL DEFAULT '{}',
    report_json     TEXT,
    report_md       TEXT,
    raw_output      TEXT,
    planned_cards   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at     TEXT,
    UNIQUE (id, project_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS feedback_run_scene (
    id              INTEGER PRIMARY KEY,
    run_id          INTEGER NOT NULL,
    project_id      INTEGER NOT NULL,
    scene_id        INTEGER NOT NULL,
    ord             INTEGER NOT NULL DEFAULT 0,
    scene_title     TEXT NOT NULL DEFAULT '',
    revision_no     INTEGER,
    source_hash     TEXT NOT NULL DEFAULT '',
    paragraphs_json TEXT NOT NULL DEFAULT '[]',
    is_primary      INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    UNIQUE (id, project_id),
    UNIQUE (run_id, scene_id),
    FOREIGN KEY (run_id, project_id) REFERENCES feedback_run(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (scene_id, project_id) REFERENCES scene(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS feedback_card (
    id                  INTEGER PRIMARY KEY,
    run_id              INTEGER NOT NULL,
    project_id          INTEGER NOT NULL,
    scene_id            INTEGER NOT NULL,
    ord                 INTEGER NOT NULL DEFAULT 0,
    kind                TEXT NOT NULL CHECK (kind IN ('correction', 'consistency', 'style', 'structure')),
    style_type          TEXT NOT NULL DEFAULT '',
    certainty           TEXT,
    impact              INTEGER,
    priority            TEXT CHECK (priority IS NULL OR priority IN ('high', 'medium', 'low', 'ref')),
    start_para          INTEGER,
    end_para            INTEGER,
    start_quote         TEXT NOT NULL DEFAULT '',
    end_quote           TEXT NOT NULL DEFAULT '',
    original_text       TEXT NOT NULL DEFAULT '',
    anchor_hash         TEXT NOT NULL DEFAULT '',
    reason              TEXT NOT NULL DEFAULT '',
    edit_plan           TEXT NOT NULL DEFAULT '',
    suggestion          TEXT,
    added_facts_json    TEXT NOT NULL DEFAULT '[]',
    removed_facts_json  TEXT NOT NULL DEFAULT '[]',
    warnings_json       TEXT NOT NULL DEFAULT '[]',
    report_ref          TEXT,
    perspectives_json   TEXT NOT NULL DEFAULT '[]',
    confidence          REAL,
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'applied', 'applied_edited', 'ignored', 'alternate')),
    final_text          TEXT,
    status_changed_at   TEXT,
    applied_at          TEXT,
    alternate_of        INTEGER,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, project_id),
    FOREIGN KEY (run_id, project_id) REFERENCES feedback_run(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (scene_id, project_id) REFERENCES scene(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (alternate_of) REFERENCES feedback_card(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_feedback_run_scene_primary
    ON feedback_run_scene(scene_id) WHERE is_primary = 1;

CREATE INDEX IF NOT EXISTS ix_feedback_run_project_created
    ON feedback_run(project_id, created_at);

CREATE INDEX IF NOT EXISTS ix_feedback_run_scene_project_scene
    ON feedback_run_scene(project_id, scene_id);

CREATE INDEX IF NOT EXISTS ix_feedback_run_scene_run_ord
    ON feedback_run_scene(run_id, ord);

CREATE INDEX IF NOT EXISTS ix_feedback_card_project_scene_created
    ON feedback_card(project_id, scene_id, created_at);

CREATE INDEX IF NOT EXISTS ix_feedback_card_run_ord
    ON feedback_card(run_id, ord);

CREATE INDEX IF NOT EXISTS ix_feedback_card_scene_status
    ON feedback_card(scene_id, status);

DROP TRIGGER IF EXISTS feedback_card_immutable_content;
CREATE TRIGGER feedback_card_immutable_content
BEFORE UPDATE OF kind, style_type, start_para, end_para, start_quote, end_quote,
    original_text, reason, edit_plan, suggestion ON feedback_card
BEGIN
    SELECT RAISE(ABORT, 'feedback card content is immutable');
END;

INSERT INTO schema_migration(version, name)
SELECT 94, 'feedback_run'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 94);
