-- 「내 문체」 프로필: 작품 하나에 하나. AI 글쓰기 요청(이어쓰기·다듬기·세계관 묘사·직접 요청·브레인스토밍)에
-- [작가 문체 프로필] 블록으로 들어간다. 시점·시제·금지어는 토리 체크(project_tory_check)와 공유하므로
-- 여기에 다시 저장하지 않는다. 형식은 style_profile.py 의 normalize_profile 참고.
-- 설계: docs/design-ai-context-and-style-2026-11.md §3.

ALTER TABLE project ADD COLUMN style_profile_json TEXT NOT NULL DEFAULT '{}';

INSERT INTO schema_migration(version, name)
SELECT 108, 'style_profile'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 108);
