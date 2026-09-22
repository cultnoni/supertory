-- 첨삭 카드 한 줄 제목. 예전 카드는 '' 로 두고 화면에서 유형 라벨을 쓴다.

ALTER TABLE feedback_card ADD COLUMN title TEXT NOT NULL DEFAULT '';

DROP TRIGGER IF EXISTS feedback_card_immutable_content;
CREATE TRIGGER feedback_card_immutable_content
BEFORE UPDATE OF kind, style_type, start_para, end_para, start_quote, end_quote,
    original_text, reason, edit_plan, suggestion, title ON feedback_card
BEGIN
    SELECT RAISE(ABORT, 'feedback card content is immutable');
END;

INSERT INTO schema_migration(version, name)
SELECT 95, 'feedback_card_title'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 95);
