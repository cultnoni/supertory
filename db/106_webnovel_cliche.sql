-- 웹소설 「장르 클리셰」: 작가만 쓰는 칸 두 개.
-- cliche_used:  의도적으로 쓴 클리셰 (카드로 만들지 않고, 리포트에서는 "의도는 알지만 신선함이 부족하다" 정도만 말한다).
-- cliche_avoid: 피하고 싶은 클리셰 (판정 기준으로 올리고 중요도를 높인다).
-- 장르문학 「장르 약속」(promise_broken/promise_kept)과 저장 패턴을 공유하지 않는다
-- (결정 사항 3번, docs/webnovel-genre-pipeline.md).

ALTER TABLE project ADD COLUMN cliche_used TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN cliche_avoid TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name)
SELECT 106, 'webnovel_cliche'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 106);
