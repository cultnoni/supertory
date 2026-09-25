ALTER TABLE feedback_card ADD COLUMN card_form TEXT NOT NULL DEFAULT '';
ALTER TABLE feedback_card ADD COLUMN issue_tags_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE feedback_card ADD COLUMN source_key TEXT NOT NULL DEFAULT '';
ALTER TABLE feedback_card ADD COLUMN intentional INTEGER NOT NULL DEFAULT 0;

DROP TRIGGER IF EXISTS feedback_card_immutable_content;
CREATE TRIGGER feedback_card_immutable_content
BEFORE UPDATE OF kind, style_type, start_para, end_para, start_quote, end_quote,
    original_text, reason, edit_plan, suggestion, title,
    card_form, issue_tags_json, source_key, intentional ON feedback_card
BEGIN
    SELECT RAISE(ABORT, 'feedback card content is immutable');
END;

INSERT INTO schema_migration(version, name)
SELECT 100, 'literary_card_fields'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 100);
