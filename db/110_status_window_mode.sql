-- 게임물 상태창을 이 작품에서 쓸지: 'auto'(키워드·이미 쓴 상태창으로 정함) · 'on' · 'off'.
-- 세계관의 「이 작품에 상태창이 나와요」 스위치가 바꾼다. 꺼도 원고 안 상태창 글은 그대로다(도구만 숨김).

ALTER TABLE project ADD COLUMN status_window_mode TEXT NOT NULL DEFAULT 'auto';

INSERT INTO schema_migration(version, name)
SELECT 110, 'status_window_mode'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 110);
