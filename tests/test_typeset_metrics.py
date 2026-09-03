"""Shared typeset metrics: preview JS and DOCX/HWPX export stay on one formula."""

from __future__ import annotations

import io
import unittest
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

import typeset_export
import typeset_metrics

ROOT = Path(__file__).resolve().parents[1]


class TypesetMetricsTests(unittest.TestCase):
    def test_js_module_matches_python_constants(self) -> None:
        js = (ROOT / "web" / "typeset_metrics.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="/typeset_metrics.js', html)
        self.assertLess(
            html.find('src="/typeset_metrics.js'),
            html.find('src="/app.js'),
        )
        self.assertIn(f"const CSS_DPI = {int(typeset_metrics.CSS_DPI)}", js)
        self.assertIn(f"const PT_PER_INCH = {int(typeset_metrics.PT_PER_INCH)}", js)
        self.assertIn("const MM_PER_INCH = 25.4", js)
        self.assertIn("function contentWidthPx", js)
        self.assertIn("function layoutMetrics", js)

    def test_preview_and_export_share_content_width(self) -> None:
        viewport = 360
        left, right = 20, 20
        preview = typeset_metrics.content_width_px(viewport, left, right)
        export_mm = typeset_metrics.paper_width_mm(viewport) - left - right
        export_px = typeset_metrics.mm_to_px(export_mm)
        self.assertAlmostEqual(preview, export_px, delta=0.01)
        self.assertAlmostEqual(preview, 360 - typeset_metrics.mm_to_px(40), delta=0.01)

    def test_docx_file_applies_preset_and_preview_page_width(self) -> None:
        preset = typeset_export.normalize_preset({
            "label": "문피아",
            "font_family": "바탕체",
            "font_size_pt": 10,
            "line_height_percent": 140,
            "letter_spacing_pt": 0.05,
            "paragraph_indent_pt": 10,
            "paragraph_spacing_pt": 4,
            "margin_left_mm": 20,
            "margin_right_mm": 20,
            "margin_top_mm": 20,
            "margin_bottom_mm": 20,
            "mobile_viewport_px": 360,
        })
        sample = "부드러운 바람이 창을 흔들었다.\n문이 열렸다."
        data = typeset_export.build_typeset_docx(sample, preset)
        document = Document(io.BytesIO(data))
        section = document.sections[0]
        metrics = typeset_metrics.layout_metrics(preset)

        self.assertAlmostEqual(section.page_width.mm, metrics["paper_width_mm"], delta=0.05)
        self.assertAlmostEqual(section.page_height.mm, typeset_metrics.A4_HEIGHT_MM, delta=0.2)
        self.assertAlmostEqual(section.left_margin.mm, 20, delta=0.2)
        self.assertAlmostEqual(section.right_margin.mm, 20, delta=0.2)
        self.assertAlmostEqual(section.top_margin.mm, 20, delta=0.2)
        self.assertAlmostEqual(section.bottom_margin.mm, 20, delta=0.2)

        first = document.paragraphs[0]
        self.assertEqual(first.text, "부드러운 바람이 창을 흔들었다.")
        self.assertAlmostEqual(float(first.paragraph_format.line_spacing), 1.4, places=2)
        self.assertEqual(first.paragraph_format.first_line_indent, Pt(10))
        self.assertEqual(first.paragraph_format.space_after, Pt(4))
        run = first.runs[0]
        self.assertEqual(run.font.size, Pt(10))
        rPr = run._element.find(qn("w:rPr"))
        spacing = rPr.find(qn("w:spacing"))
        self.assertIsNotNone(spacing)
        self.assertEqual(
            spacing.get(qn("w:val")),
            str(typeset_metrics.letter_spacing_docx_twips(0.05, 10)),
        )
        content_mm = section.page_width.mm - section.left_margin.mm - section.right_margin.mm
        self.assertAlmostEqual(
            typeset_metrics.mm_to_px(content_mm),
            metrics["content_width_px"],
            delta=0.5,
        )

    def test_hwpx_file_applies_preset_and_preview_page_width(self) -> None:
        preset = typeset_export.normalize_preset({
            "label": "문피아",
            "font_family": "바탕체",
            "font_size_pt": 10,
            "line_height_percent": 140,
            "letter_spacing_pt": 0.05,
            "paragraph_indent_pt": 10,
            "paragraph_spacing_pt": 4,
            "margin_left_mm": 20,
            "margin_right_mm": 20,
            "margin_top_mm": 20,
            "margin_bottom_mm": 20,
            "mobile_viewport_px": 360,
        })
        data = typeset_export.build_typeset_hwpx("부드러운 바람이 창을 흔들었다.\n문이 열렸다.", preset)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            header = archive.read("Contents/header.xml").decode("utf-8")
            section = archive.read("Contents/section0.xml").decode("utf-8")
        width = typeset_metrics.mm_to_hwp(typeset_metrics.paper_width_mm(360))
        height = typeset_metrics.mm_to_hwp(typeset_metrics.A4_HEIGHT_MM)
        self.assertIn(f'width="{width}"', section)
        self.assertIn(f'height="{height}"', section)
        self.assertIn(f'left="{typeset_export._hwp_units_from_mm(20)}"', section)
        self.assertIn('type="PERCENT" value="140"', header)
        self.assertIn('height="1000"', header)
        self.assertIn('hangul="5"', header)
        self.assertIn("부드러운 바람이 창을 흔들었다.", section)
        self.assertNotRegex(section, r'width="59528"')

    def test_app_js_uses_shared_metrics_for_preview_width(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("TypesetMetrics.layoutMetrics", app_js)
        self.assertIn("TypesetMetrics.ptToPx", app_js)
        self.assertIn("TypesetMetrics.mmToPx", app_js)
        self.assertIn("innerW = metrics.contentWidthPx", app_js)
        self.assertNotIn("* (96 / 72)", app_js)
        self.assertNotIn("* (96 / 25.4)", app_js)


if __name__ == "__main__":
    unittest.main()
