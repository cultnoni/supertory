"""Frontend contract for the compact typeset toolbar (collapse + grouped fields)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TypesetToolbarUiTests(unittest.TestCase):
    def test_details_toggle_and_grouped_fields(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="viewerTypesetDetailsToggle"', html)
        self.assertIn('id="viewerTypesetDetails"', html)
        self.assertIn("viewer-typeset-presets", html)
        self.assertIn("viewer-typeset-fields", html)
        self.assertIn("viewer-typeset-group", html)
        self.assertIn("viewer-typeset-actions", html)
        self.assertIn("index.상세_설정", html)
        self.assertIn("index.접기", html)
        self.assertLess(
            html.find('id="viewerTypesetDetailsToggle"'),
            html.find('id="viewerTypesetDetails"'),
        )
        self.assertLess(
            html.find('id="viewerTypesetSave"'),
            html.find('id="viewerTypesetExport"'),
        )
        self.assertGreater(
            html.find('id="viewerTypesetDetails"'),
            html.find('id="viewerTypesetPlatforms"'),
        )
        self.assertIn('id="viewerTypesetSave"', html[html.find('id="viewerTypesetDetails"') :])

        self.assertIn('supertory.viewerTypesetDetailsOpen', app_js)
        self.assertIn("function applyTypesetDetailsOpen", app_js)
        self.assertIn("function toggleTypesetDetailsOpen", app_js)

        self.assertIn(".viewer-typeset-details-toggle", css)
        self.assertIn(".viewer-typeset-group + .viewer-typeset-group", css)
        self.assertIn("border-left: 0.5px solid var(--border-strong)", css)
        self.assertIn(".viewer-typeset-field", css)

    def test_numeric_fields_are_selects_with_custom_option(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        fields = (
            "viewerTypesetFontSize",
            "viewerTypesetLineHeight",
            "viewerTypesetLetterSpacing",
            "viewerTypesetIndent",
            "viewerTypesetParaSpacing",
            "viewerTypesetMarginLeft",
            "viewerTypesetMarginRight",
            "viewerTypesetMarginTop",
            "viewerTypesetMarginBottom",
            "viewerTypesetViewport",
        )
        for field_id in fields:
            self.assertIn(f'<select id="{field_id}"', html)
            self.assertIn(f'id="{field_id}Custom"', html)
            self.assertIn(f'value="__custom__"', html)
        self.assertIn('value="9"', html)
        self.assertIn('value="14"', html)
        self.assertIn('value="130"', html)
        self.assertIn('value="180"', html)
        self.assertIn('value="-0.05"', html)
        self.assertIn('value="0.1"', html)
        self.assertIn('value="320"', html)
        self.assertIn('value="414"', html)
        self.assertIn("index.직접입력", html)
        self.assertIn("index.들여쓰기_1칸", html)
        self.assertIn("index.들여쓰기_2칸", html)
        self.assertNotIn('id="viewerTypesetFontSize" type="number"', html)
        self.assertIn("TYPESET_SELECT_CUSTOM", app_js)
        self.assertIn("TYPESET_FIELD_PRESETS", app_js)
        self.assertIn("function setTypesetChoice", app_js)
        self.assertIn("function setTypesetIndentChoice", app_js)
        self.assertIn("function onTypesetChoiceSelect", app_js)
        self.assertIn(".viewer-typeset-custom-input", css)
        self.assertIn(".viewer-typeset-field select", css)

    def test_detail_locale_keys_synced(self) -> None:
        for name in ("ko", "en", "es"):
            data = json.loads((ROOT / "web" / "locales" / f"{name}.json").read_text(encoding="utf-8"))
            self.assertIn("index.상세_설정", data)
            self.assertTrue(str(data["index.상세_설정"]).strip())
            self.assertIn("index.접기", data)
            self.assertEqual(
                str(data["index.직접입력"]).strip() != "",
                True,
            )
            self.assertTrue(str(data["index.들여쓰기_1칸"]).strip())
            self.assertTrue(str(data["index.들여쓰기_2칸"]).strip())

    def test_typeset_preview_flow_reuses_device_page_turn(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="viewerTypesetFlow"', html)
        flow_html = html[
            html.find('id="viewerTypesetFlow"') : html.find('id="viewerTypesetDetailsToggle"')
        ]
        self.assertIn('value="scroll"', flow_html)
        self.assertIn('value="page"', flow_html)
        self.assertLess(
            html.find('id="viewerTypesetFlow"'),
            html.find('id="viewerTypesetDetails"'),
        )
        self.assertIn("typesetFlow: \"scroll\"", app_js)
        self.assertIn("viewerTypesetFlow", app_js)
        self.assertIn("function isDeviceTurnMode", app_js)
        self.assertIn("function layoutDevicePages", app_js)
        self.assertIn("function turnDevicePage", app_js)
        self.assertIn("function paintDevicePageStack", app_js)
        is_scroll = app_js.split("function isDeviceScrollFlow", 1)[1].split(
            "function normalizeDeviceFlow", 1
        )[0]
        self.assertIn("typesetFlow", is_scroll)
        save_fn = app_js.split("async function saveTypesetPresetFromForm", 1)[1].split(
            "function hideTypesetExportMenu", 1
        )[0]
        self.assertNotIn("typesetFlow", save_fn)
        self.assertNotIn("viewerTypesetFlow", save_fn)
        self.assertIn('[data-viewer-flow="page"]', css)
        self.assertIn(".viewer-typeset-flow", css)
        for name in ("ko", "en", "es"):
            data = json.loads((ROOT / "web" / "locales" / f"{name}.json").read_text(encoding="utf-8"))
            self.assertTrue(str(data["index.보기_방식"]).strip())
            self.assertTrue(str(data["index.보기_넘기기"]).strip())

    def test_typeset_scroll_and_page_share_margins_and_frame(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("function typesetPreviewPadding", app_js)
        self.assertIn("function typesetPreviewFrameSize", app_js)
        apply_styles = app_js.split("function applyTypesetBodyStyles", 1)[1].split(
            "async function saveTypesetPresetFromForm", 1
        )[0]
        self.assertIn("TypesetMetrics.layoutMetrics", apply_styles)
        self.assertIn("body.style.padding = padCss", apply_styles)
        self.assertIn("body.style.width", apply_styles)
        layout_fn = app_js.split("function applyViewerLayout", 1)[1].split(
            "function isDevicePagedMode", 1
        )[0]
        typeset_layout = layout_fn.split('mode === "typeset"', 1)[1].split("} else {", 1)[0]
        self.assertNotIn("16px 14px 32px", typeset_layout)
        self.assertIn("typesetPreviewFrameSize", typeset_layout)
        self.assertIn("maxWidth = `${frameSize.width}px`", typeset_layout)
        page_box = app_js.split("function computeViewerPageBox", 1)[1].split(
            "function vwTooSmall", 1
        )[0]
        typeset_box = page_box.split('mode === "typeset"', 1)[1].split("} else {", 1)[0]
        self.assertIn("TypesetMetrics.layoutMetrics", typeset_box)
        self.assertIn("contentWidthPx", typeset_box)
        typeset_css = css.split("/* Platform typeset preview", 1)[1].split("/* E-ink reader */", 1)[0]
        self.assertNotIn("16px 14px 32px", typeset_css)
        self.assertNotIn("padding-left: 56px", typeset_css)
        self.assertIn("--typeset-frame-width", typeset_css)
        self.assertIn("--typeset-pad-top", typeset_css)

    def test_typeset_page_fill_matches_scroll_density(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        page_box = app_js.split("function computeViewerPageBox", 1)[1].split(
            "function vwTooSmall", 1
        )[0]
        typeset_box = page_box.split('mode === "typeset"', 1)[1].split("} else {", 1)[0]
        self.assertIn("innerH = Math.max(40, vh - padT)", typeset_box)
        self.assertNotIn("vh - padT - padB", typeset_box)
        self.assertIn("previewPadBottom: 0", typeset_box)
        self.assertIn("fontSizeCss", typeset_box)
        paginate = app_js.split("function paginateHtmlToPages", 1)[1].split(
            "function cancelBookFlipAnimation", 1
        )[0]
        self.assertNotIn("Need a clean page if current page already has content", paginate)
        self.assertIn("split children into remaining space", paginate)
        self.assertIn("snapToWhitespace", paginate)
        self.assertIn("wrappersMatch", paginate)
        self.assertIn("margin-bottom: 0", paginate)
        typeset_css = css.split("/* Platform typeset preview", 1)[1].split("/* E-ink reader */", 1)[0]
        self.assertIn(".device-sheet-inner > *:last-child { margin-bottom: 0; }", typeset_css)
        self.assertIn(".device-sheet-inner > .viewer-episode", typeset_css)
        self.assertIn("scrollbar-gutter: stable", typeset_css)
        self.assertIn("--typeset-scrollbar-gutter", typeset_css)
        self.assertIn("function syncTypesetScrollbarGutter", app_js)
        self.assertIn("syncTypesetScrollbarGutter()", app_js)

    def test_typeset_scroll_and_page_share_glyph_metrics(self) -> None:
        """`.rich-editor` kerning must not reach only the scroll body."""
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        typeset_css = css.split("/* Platform typeset preview", 1)[1].split("/* E-ink reader */", 1)[0]
        shared = typeset_css.split('.viewer-stage[data-viewer-mode="typeset"] .viewer-body,', 1)[1]
        shared = shared.split("}", 1)[0]
        for prop in (
            "text-rendering: auto",
            "-webkit-font-smoothing: auto",
            "font-kerning: none",
            "font-variant-ligatures: none",
            "word-break: keep-all",
            "overflow-wrap: break-word",
        ):
            self.assertIn(prop, shared)
        self.assertIn(".device-sheet-inner {", shared)
        self.assertNotIn("word-break: break-all", typeset_css)
        self.assertNotIn("overflow-wrap: anywhere", typeset_css)
        page_box = app_js.split("function computeViewerPageBox", 1)[1].split(
            "function vwTooSmall", 1
        )[0]
        typeset_box = page_box.split('mode === "typeset"', 1)[1].split("} else {", 1)[0]
        self.assertIn('wordBreak: "keep-all"', typeset_box)
        self.assertIn('overflowWrap: "break-word"', typeset_box)
        paginate = app_js.split("function paginateHtmlToPages", 1)[1].split(
            "function cancelBookFlipAnimation", 1
        )[0]
        measure_css = paginate.split("const measureCss = [", 1)[1].split("];", 1)[0]
        for prop in (
            "text-rendering:auto",
            "-webkit-font-smoothing:auto",
            "font-kerning:none",
            "font-variant-ligatures:none",
            'word-break:${styleOpts.wordBreak || "keep-all"}',
            'overflow-wrap:${styleOpts.overflowWrap || "break-word"}',
        ):
            self.assertIn(prop, measure_css)


if __name__ == "__main__":
    unittest.main()
