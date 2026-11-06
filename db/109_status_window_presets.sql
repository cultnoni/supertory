-- 게임물 상태창 프리셋: 작품 하나에 목록 하나(JSON 배열). 형식은 status_window.py 의 normalize_presets 참고.
-- 원고 안의 상태창 블록은 만든 때의 모양을 그대로 들고 있으므로(data-status-window …), 프리셋을 고치거나
-- 지워도 이미 쓴 회차는 바뀌지 않는다. 설계: docs/design-ai-context-and-style-2026-11.md §13.

ALTER TABLE project ADD COLUMN status_window_presets_json TEXT NOT NULL DEFAULT '[]';

INSERT INTO schema_migration(version, name)
SELECT 109, 'status_window_presets'
WHERE NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 109);
