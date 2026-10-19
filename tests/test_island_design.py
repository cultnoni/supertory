"""디자인 '섬과 종이' 계약 테스트 — docs/design-guidelines.md"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class IslandDesignContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = read(WEB / "index.html")
        self.js = read(WEB / "app.js")
        self.css = read(WEB / "island.css")

    def test_island_layer_loads_after_base_styles(self) -> None:
        base = self.html.index('href="/styles.css')
        island = self.html.index('href="/island.css')
        self.assertLess(base, island)

    def test_panel_widths_are_equal(self) -> None:
        self.assertIn("--outline-width: var(--isl-panel-w)", self.css)
        self.assertIn("--ai-panel-width: var(--isl-panel-w)", self.css)

    def test_mascot_is_used_for_tori_panel_and_home(self) -> None:
        title = self.html.split('class="ai-panel-title"', 1)[1].split("</h2>", 1)[0]
        self.assertIn("tori-mascot.png", title)
        home = self.html.split('id="titlebarHomeButton"', 1)[1].split("</button>", 1)[0]
        self.assertIn("tori-mascot.png", home)
        # 작품 목록은 라이브러리 아이콘 (마스코트 아님)
        library = self.html.split('id="projectLibraryButton"', 1)[1].split("</button>", 1)[0]
        self.assertIn("lucide-library-big", library)
        self.assertNotIn("tori-mascot.png", library)

    def test_removed_next_episode_plus_button_stays_removed(self) -> None:
        self.assertNotIn("episodeTabAddButton", self.html)
        self.assertNotIn("openAdjacentEpisodeAsTab", self.js)

    def test_check_tab_wired(self) -> None:
        self.assertIn('id="aiTabCheck"', self.html)
        self.assertIn('id="aiCheckView"', self.html)
        self.assertIn('tab === "check"', self.js)
        self.assertIn("setupAiCheckHub();", self.js)
        for dock in ("writingTimer", "toryCheck", "spellcheck", "statsTracker"):
            self.assertIn(f'data-isl-dock="{dock}"', self.html)
            self.assertIn(f'data-dock-item="{dock}"', self.html)

    def test_tory_notify_lives_behind_header_bell(self) -> None:
        self.assertIn('id="islNotifyBell"', self.html)
        self.assertIn('id="islNotifyRailButton"', self.html)
        # 알림 센터 본체(id·목록·추가 버튼)는 그대로 — 팝오버로만 보인다
        for ident in ("toryNotifyBox", "toryNotifyList", "toryNotifyAddButton", "toryNotifyPopupOpenButton"):
            self.assertIn(f'id="{ident}"', self.html)
        self.assertIn("is-popover-open", self.css)
        self.assertIn("function setToryNotifyPopover(", self.js)

    def test_boards_and_modals_share_common_parts(self) -> None:
        # 설정집 메인 보드는 같은 여백/바탕, 모달·메뉴는 같은 반경/그림자 (docs/design-guidelines.md §4)
        for board in (".character-board", ".idea-board", ".keyword-board", ".worldbuilding-workspace",
                      ".settings-collection-board", ".synopsis-workspace", ".settings-search-board"):
            self.assertIn(f"#workArea > {board}", self.css, board)
        self.assertIn(".modal-card {", self.css)
        self.assertIn("var(--isl-pop-shadow)", self.css)
        for menu in (".context-menu", ".project-list-dropdown", ".format-overflow-menu"):
            self.assertIn(menu, self.css)

    def test_binder_status_dots_part_card_and_stepper(self) -> None:
        # 목차 상태는 점(+data-status), 글자수 표시, 작성 상태는 단계 알약(기존 #sceneStatus select 재사용)
        self.assertIn('data-status="${escapeHtml(String(scene.status', self.js)
        self.assertIn('class="scene-wc"', self.js)
        self.assertIn('id="islStatusStepper"', self.html)
        self.assertIn('id="sceneStatus"', self.html)
        self.assertIn("function syncStatusStepper(", self.js)
        self.assertIn('sel.dispatchEvent(new Event("change"', self.js)
        self.assertIn('id="islPartCard"', self.html)
        self.assertIn("function refreshPartCard(", self.js)

    def test_binder_tiles_footer_and_paper_order(self) -> None:
        # 시안 대조: 설정집 바로가기 타일·하단 바·체크 탭 물어보기·종이 머리(도구줄 → 제목/상태/요약 → 본문)
        for ident in ("islTiles", "islNewEpisode", "islTilesToggle", "islAskForm", "islPartTitle", "islPartSub"):
            self.assertIn(f'id="{ident}"', self.html)
        for main in ("characters", "world", "baits", "ideas"):
            self.assertIn(f'data-isl-open="{main}"', self.html)
            self.assertIn(f'data-settings-main="{main}"', self.html)  # 타일이 여는 기존 진입점이 있어야 함
        self.assertIn(".scene-workspace:not(.split-active) .episode-nav-bar { order: 3; }", self.css)
        self.assertIn(".scene-workspace:not(.split-active) #sceneStatusShell { order: 4; }", self.css)
        # 아이콘줄에서 집필 기록(타이머)은 체크 탭으로 이동
        self.assertIn('data-icon-key="timer"] { display: none', self.css)

    def test_outline_header_options_moved_to_menu(self) -> None:
        # 목차 머리줄(목차·개수·새 폴더·글자 간격·순번 정리)은 숨기고 요약 카드 ⋯ 메뉴로 옮김 — 기능은 그대로
        for target in ("outlineTocHeading", "newChapterButton", "outlineSpacingButton", "renumberChaptersButton"):
            self.assertIn(f'id="{target}"', self.html)
            self.assertIn(f'data-isl-proxy="{target}"', self.html)
        self.assertIn("#manuscriptBinder > .outline-header-stack", self.css)

    def test_toolbar_icons_follow_mockup_stroke(self) -> None:
        seg = self.html.split('id="formatIconsToolbar"', 1)[1].split('id="formatToolbarShell"', 1)[0]
        # 비교 아이콘은 처음의 원래 아이콘(1.75)을 유지 → 나머지 12개가 시안 굵기
        self.assertGreaterEqual(seg.count('stroke-width="1.8"'), 11)
        for tab in ("aiTabCheck", "aiTabChat", "aiTabTools", "aiTabWidgets"):
            self.assertIn(f'id="{tab}"', self.html)
        order = [self.html.index(f'id="{t}"') for t in ("aiTabCheck", "aiTabChat", "aiTabTools", "aiTabWidgets")]
        self.assertEqual(order, sorted(order))

    def test_titlebar_halves_overflow_label_and_pinned_binder_tiles(self) -> None:
        # 타이틀바: 오른쪽 반 = 검색, 왼쪽 반 = 고정 메모 / 좁은 창 »는 ⋯ 더보기 / 설정집 타일·하단 바는 스크롤되지 않음
        self.assertIn("left: 50% !important;", self.css)
        self.assertIn("let rightVp = wr.right;", self.js)  # 검색바 제거: 고정 메모가 메인 오른쪽 끝까지
        self.assertIn("--isl-main-center", self.js)
        self.assertIn("calc(var(--isl-main-right, 100%) - var(--isl-main-center, 50%))", self.css)
        self.assertEqual(self.html.count('class="format-overflow-label" data-i18n="island.더보기"'), 3)
        self.assertNotIn('format-overflow-chevrons', self.html)
        self.assertIn("#manuscriptBinder > #outline {", self.css)
        self.assertIn("#manuscriptBinder > .isl-tiles", self.css)

    def test_scene_column_is_not_capped_on_wide_screens(self) -> None:
        # 기존 .writing-column 920px 제한이 남으면 넓은 화면에서 탭·도구줄이 가운데 좁게 떠 보인다
        self.assertIn("#sceneWorkspace .writing-column { max-width: none !important;", self.css)

    def test_format_more_popover_matches_mockup(self) -> None:
        # 서식줄 ⋯ 더보기 팝오버: 글자(장평·자간·글자색·형광펜) · 단락(정렬·단 나누기·글머리·번호) · 삽입(특수문자) · 문서
        for ident in ("islFmtMore", "islFmtPop", "islFpWidth", "islFpTracking", "islFpTextSw", "islFpHlSw", "islFpChars"):
            self.assertIn(f'id="{ident}"', self.html)
        for fmt in ("justifyLeft", "justifyCenter", "justifyRight", "justifyFull", "dan", "list", "specialChar"):
            self.assertIn(f'data-isl-fmt="{fmt}"', self.html)
            # 팝오버가 누르는 기존 서식 버튼이 DOM에 남아 있어야 한다(숨김만)
            self.assertIn(f'data-format="{fmt}"', self.html)
        self.assertIn("function applyLetterSpacing(", self.js)
        self.assertIn("function setupFormatMorePopover(", self.js)
        self.assertIn('!row.closest("#formatToolbarShell")', self.js)
        # 기본은 모두 바깥 · 폭이 모자랄 때만 단계(is-tight → is-compact)로 접고 ⋯ 더보기 표시
        self.assertIn('#formatToolbar.is-compact [data-format="justifyLeft"]', self.css)
        self.assertIn("function fitFormatRow(", self.js)
        self.assertIn('#formatToolbar.is-tight .isl-fmt-more', self.css)

    def test_icon_and_format_rows_have_no_collapse_feature(self) -> None:
        # 도구 아이콘줄·서식줄 접기/다시 열기 버튼은 삭제됨 — 다시 만들지 않는다
        for token in ('data-toolbar-hide="format-icons"', 'data-toolbar-hide="format-format"',
                      'id="formatIconsReopenButton"', 'id="formatFormatReopenButton"',
                      'id="formatIconsHideButton"', 'id="formatFormatHideButton"'):
            self.assertNotIn(token, self.html)
        self.assertIn('applyMsToolbarCollapsed("format-icons", false', self.js)
        self.assertIn('applyMsToolbarCollapsed("format-format", false', self.js)
        self.assertIn("function fitFormatSelects(", self.js)
        # 형광펜·텍스트 음영상자(박스 색)는 서식줄 바깥에 보인다 — 숨김 목록에 넣지 않는다
        hidden = self.css.split('#formatToolbar.is-tight .format-field-chip:has(#formatJangpyeong),', 1)[1].split('{', 1)[0]
        self.assertNotIn('data-format="highlight"', hidden)
        self.assertNotIn('data-format="shadeBox"', hidden)

    def test_icon_row_is_icon_only_with_hover_name_and_selected_bold(self) -> None:
        # 이름(글자)은 다시 표시, 비교 버튼은 분할 메뉴 안으로 이동
        self.assertNotIn("#formatIconsToolbar .format-tool-label { display: none !important; }", self.css)
        self.assertIn("#compareSplitButton { display: none !important; }", self.css)
        self.assertIn('id="viewModeCompareItem"', self.html)
        self.assertIn("function setupSplitCompareItem(", self.js)
        self.assertIn('getComputedStyle(el).display === "none"', self.js)  # 숨긴 항목은 더보기 목록에 넣지 않는다
        self.assertIn(".seg-control-tab.is-active, .seg-control-tab[aria-selected=\"true\"] { font-weight: 700 !important; }", self.css)
        self.assertIn(".project-title-label { font-weight: 400 !important; }", self.css)
        self.assertIn(".episode-tab.is-active {", self.css)

    def test_toolbars_and_status_are_flat_full_width_bands(self) -> None:
        # 탭줄·아이콘줄·서식줄·하단 상태줄 = 섬 폭 가득한 가로 띠(박스·안쪽 여백 없음), 종이는 하단 띠에 붙는다
        self.assertIn("#sceneWorkspace .writing-column { padding: 0 !important; }", self.css)
        self.assertIn("#manuscriptStatusWrap {", self.css)
        self.assertIn(".scene-workspace:not(.split-active) .manuscript-page { margin-bottom: 0 !important;", self.css)
        self.assertIn("#formatToolbar { background: transparent !important; border-radius: 0 !important; }", self.css)

    def test_island_i18n_keys_exist_in_all_locales(self) -> None:
        keys = set(re.findall(r'data-i18n(?:-[a-z-]+)?="(island\.[^"]+)"', self.html))
        keys |= set(re.findall(r'i18n\.t\("(island\.[^"]+)"\)', self.js))
        self.assertTrue(keys)
        for lc in ("ko", "en", "es"):
            data = json.loads(read(WEB / "locales" / f"{lc}.json"))
            missing = sorted(k for k in keys if k not in data)
            self.assertEqual(missing, [], f"{lc} missing {missing}")

    def test_island_css_uses_theme_variables_not_brand_hex(self) -> None:
        # 색은 기존 테마 변수로. 라이트·샌드 팔레트 정의 블록(data-theme/data-ui-theme × html/body)과 그림자·링은 예외.
        body = re.sub(
            r'html\[data-theme="light"\](?:,\s*(?:html|body)\[data-(?:ui-)?theme="(?:light|sand)"\])*\s*\{.*?\}',
            "",
            self.css,
            flags=re.S,
        )
        body = re.sub(r"rgba?\([^)]*\)", "", body)
        hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
        # var() 대체값(fallback)과 상태 점 토큰 정도만 허용
        self.assertLessEqual(len(hexes), 12, hexes)


if __name__ == "__main__":
    unittest.main()
