"""Shared typeset geometry: preview CSS and DOCX/HWPX export use these formulas.

96 CSS px = 1 in = 25.4 mm = 72 pt. Keep this file and web/typeset_metrics.js in sync
(tests/test_typeset_metrics.py compares both).
"""

from __future__ import annotations

CSS_DPI = 96.0
PT_PER_INCH = 72.0
MM_PER_INCH = 25.4
TWIPS_PER_PT = 20.0
HWPUNIT_PER_INCH = 7200.0
HWPUNIT_PER_MM = HWPUNIT_PER_INCH / MM_PER_INCH
HWPUNIT_PER_PT = HWPUNIT_PER_INCH / PT_PER_INCH
VIEWPORT_MIN_PX = 200
VIEWPORT_MAX_PX = 800
VIEWPORT_DEFAULT_PX = 360
A4_HEIGHT_MM = 297.0
LETTER_SPACING_EM_ABS_MAX = 0.5


def pt_to_px(pt: float) -> float:
    return float(pt or 0) * CSS_DPI / PT_PER_INCH


def mm_to_px(mm: float) -> float:
    return float(mm or 0) * CSS_DPI / MM_PER_INCH


def px_to_mm(px: float) -> float:
    return float(px or 0) * MM_PER_INCH / CSS_DPI


def mm_to_hwp(mm: float) -> int:
    return int(round(float(mm or 0) * HWPUNIT_PER_MM))


def pt_to_hwp(pt: float) -> int:
    return int(round(float(pt or 0) * HWPUNIT_PER_PT))


def paper_width_px(viewport_px) -> int:
    try:
        value = int(round(float(viewport_px)))
    except (TypeError, ValueError):
        value = VIEWPORT_DEFAULT_PX
    return max(VIEWPORT_MIN_PX, min(VIEWPORT_MAX_PX, value or VIEWPORT_DEFAULT_PX))


def paper_width_mm(viewport_px) -> float:
    return px_to_mm(paper_width_px(viewport_px))


def content_width_px(viewport_px, margin_left_mm, margin_right_mm) -> float:
    return max(
        40.0,
        paper_width_px(viewport_px) - mm_to_px(margin_left_mm) - mm_to_px(margin_right_mm),
    )


def content_width_mm(viewport_px, margin_left_mm, margin_right_mm) -> float:
    return px_to_mm(content_width_px(viewport_px, margin_left_mm, margin_right_mm))


def letter_spacing_is_em(value: float) -> bool:
    return 0 < abs(float(value or 0)) <= LETTER_SPACING_EM_ABS_MAX


def letter_spacing_css(value: float) -> str:
    n = float(value or 0)
    if not n:
        return "0"
    if abs(n) <= LETTER_SPACING_EM_ABS_MAX:
        return f"{n}em"
    return f"{n}pt"


def letter_spacing_docx_twips(value: float, font_size_pt: float) -> int:
    raw = float(value or 0)
    if not raw:
        return 0
    size = max(1.0, float(font_size_pt or 10))
    twips = raw * size * TWIPS_PER_PT if letter_spacing_is_em(raw) else raw * TWIPS_PER_PT
    return int(round(twips))


def letter_spacing_hwp(value: float, font_size_pt: float) -> int:
    raw = float(value or 0)
    size = max(1.0, float(font_size_pt or 10))
    if letter_spacing_is_em(raw):
        spacing = int(round(raw * 100.0))
    else:
        spacing = int(round(raw / size * 100.0)) if raw else 0
    return max(-50, min(50, spacing))


def layout_metrics(preset: dict | None = None) -> dict:
    src = preset if isinstance(preset, dict) else {}
    viewport = paper_width_px(src.get("mobile_viewport_px", VIEWPORT_DEFAULT_PX))
    font_pt = float(src.get("font_size_pt") or 10)
    line_percent = float(src.get("line_height_percent") or 150)
    spacing = float(src.get("letter_spacing_pt") or 0)
    indent_pt = float(src.get("paragraph_indent_pt") or 0)
    para_gap_pt = float(src.get("paragraph_spacing_pt") or 0)
    pad_left = mm_to_px(src.get("margin_left_mm") or 0)
    pad_right = mm_to_px(src.get("margin_right_mm") or 0)
    pad_top = mm_to_px(src.get("margin_top_mm") or 0)
    pad_bottom = mm_to_px(src.get("margin_bottom_mm") or 0)
    return {
        "viewport_px": viewport,
        "paper_width_mm": paper_width_mm(viewport),
        "paper_height_mm": A4_HEIGHT_MM,
        "content_width_px": max(40.0, viewport - pad_left - pad_right),
        "pad_left_px": pad_left,
        "pad_right_px": pad_right,
        "pad_top_px": pad_top,
        "pad_bottom_px": pad_bottom,
        "font_size_pt": font_pt,
        "font_size_px": pt_to_px(font_pt),
        "line_height_percent": line_percent,
        "letter_spacing_css": letter_spacing_css(spacing),
        "indent_pt": indent_pt,
        "para_gap_pt": para_gap_pt,
    }
