-- Genre-literature romance axes + cross-genre romance blend intensity.
-- romance_structure: '' | emotional | plot_driven
--   Shared whenever romance is involved: main=romance OR blend in {co_axis, main_axis}.
--   Not used for subplot/none (subplot uses a generic low-weight romance auxiliary set).
-- romance_setting:   '' | contemporary | period
--   Main=romance only (activates 04 설정집 사극 지원 모듈). Cleared for blend cases.
--   # Future: SF+사극 로맨스 등 결합축+사극이 필요하면 별도 이슈.
-- romance_blend:     none | subplot | co_axis | main_axis  (default none; N/A when main=romance)
-- Existing rows keep empty axes and none blend → prior behavior unchanged.

ALTER TABLE project ADD COLUMN romance_structure TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN romance_setting TEXT NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN romance_blend TEXT NOT NULL DEFAULT 'none';

INSERT INTO schema_migration(version, name) VALUES (93, 'project_romance_axes');
