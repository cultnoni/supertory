"""기본·라이트 테마 색 짝과 시작 화면의 패널 다시 열기 손잡이 계약."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _block(css: str, theme: str) -> str:
    """island.css에서 ``html[data-ui-theme="<theme>"]`` 선택자로 시작하는 첫 규칙 블록."""
    match = re.search(r'html\[data-theme="%s"\],\s*html\[data-ui-theme="%s"\][^{]*\{(.*?)\n\}' % (theme, theme), css, re.S)
    assert match, theme
    return match.group(1)


class ThemePaletteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.css = (ROOT / "web" / "island.css").read_text(encoding="utf-8")
        self.app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_default_theme_id_light_is_the_grey_teal_palette(self) -> None:
        light = _block(self.css, "light")
        self.assertIn("--chrome-bg: #eceef1", light)
        self.assertIn("--accent: #2f7566", light)

    def test_sand_theme_is_the_cream_beige_palette(self) -> None:
        sand = _block(self.css, "sand")
        self.assertIn("--chrome-bg: #f8f4ec", sand)
        self.assertIn("--accent: #5c4636", sand)

    def test_labels_match_the_palettes(self) -> None:
        # 화면에 보이는 이름: id light = 기본, id sand = 라이트
        self.assertRegex(self.app_js, r'id: "light",\s+emoji: "[^"]*",\s+short: i18n\.t\(\'app\.기본\'\)')
        self.assertRegex(self.app_js, r'id: "sand",\s+emoji: "[^"]*",\s+short: i18n\.t\(\'app\.라이트\'\)')

    def test_first_run_theme_is_light(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('setAttribute("data-ui-theme", "light")', html)
        self.assertRegex(self.app_js, r'(?s)function normalizeUiThemeId\(theme\) \{.*?return "light";')

    def test_titlebar_fallbacks_follow_the_palettes(self) -> None:
        self.assertIn('light: { color: "#ECEEF1"', self.app_js)
        self.assertIn('sand: { color: "#F8F4EC"', self.app_js)


class StartScreenPanelHandleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.css = (ROOT / "web" / "island.css").read_text(encoding="utf-8")
        self.app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_start_screen_has_both_reopen_handles(self) -> None:
        welcome = self.html.split('<div id="welcome" class="welcome-screen">', 1)[1][:3000]
        self.assertIn('id="welcomeBinderOpenButton"', welcome)
        self.assertIn('id="welcomeAiOpenButton"', welcome)
        # 탭 줄 손잡이와 같은 클래스라 접힌 쪽만 보인다(island.css의 :not(.…-collapsed) 숨김 규칙 공용)
        self.assertIn("episode-tab-row-toggle-left", welcome)
        self.assertIn("episode-tab-row-toggle-right", welcome)
        self.assertIn("body:not(.binder-panel-collapsed) .episode-tab-row-toggle-left", self.css)
        self.assertIn("body:not(.ai-panel-collapsed) .episode-tab-row-toggle-right", self.css)

    def test_handles_open_the_panels(self) -> None:
        self.assertIn('$("welcomeBinderOpenButton")?.addEventListener("click", () => setBinderPanelOpen(true))', self.app_js)
        self.assertIn('$("welcomeAiOpenButton")?.addEventListener("click", () => setAiPanelOpen(true))', self.app_js)

    def test_handles_sit_at_the_island_corners_level_with_the_panel_buttons(self) -> None:
        # 흐름 밖(absolute)이라 시작 화면 가운데 정렬·배치를 건드리지 않는다
        self.assertIn("#welcome { position: relative; }", self.css)
        self.assertRegex(self.css, r"#welcome > \.welcome-panel-bar \{[^}]*position: absolute;")
        self.assertRegex(self.css, r"#welcome > \.welcome-panel-bar \{[^}]*height: 0;")
        # 패널 머리의 접기 버튼과 같은 끝 간격(11px = 섬 테두리 1px + 10px)과 같은 높이
        self.assertIn("#welcome #welcomeBinderOpenButton { left: 10px; top: 10px; }", self.css)
        self.assertIn("#welcome #welcomeAiOpenButton { right: 10px; top: 14px; }", self.css)


if __name__ == "__main__":
    unittest.main()
