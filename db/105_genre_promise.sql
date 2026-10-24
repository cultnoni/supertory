-- 장르문학 「장르 약속」: 작가만 쓰는 칸 두 개.
-- promise_broken: 일부러 깬 약속 (카드로 만들지 않고, 리포트에서는 "의도는 알지만 전달이 약하다" 정도만 말한다).
-- promise_kept:   지키고 싶은 약속 (판정 기준으로 올리고 중요도를 높인다).
-- 문체 6칸처럼 칸마다 따로 저장하며, 합친 문서를 만들지 않는다.

ALTER TABLE project ADD COLUMN promise_broken TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN promise_kept TEXT NOT NULL DEFAULT '';

INSERT INTO schema_migration(version, name)
SELECT 105, 'genre_promise'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 105);
