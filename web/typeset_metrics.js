/* Shared typeset geometry. Keep in sync with typeset_metrics.py */
(function (global) {
  const CSS_DPI = 96;
  const PT_PER_INCH = 72;
  const MM_PER_INCH = 25.4;
  const TWIPS_PER_PT = 20;
  const HWPUNIT_PER_INCH = 7200;
  const HWPUNIT_PER_MM = HWPUNIT_PER_INCH / MM_PER_INCH;
  const VIEWPORT_MIN_PX = 200;
  const VIEWPORT_MAX_PX = 800;
  const VIEWPORT_DEFAULT_PX = 360;
  const A4_HEIGHT_MM = 297;
  const LETTER_SPACING_EM_ABS_MAX = 0.5;

  function ptToPx(pt) {
    return (Number(pt) || 0) * CSS_DPI / PT_PER_INCH;
  }

  function mmToPx(mm) {
    return (Number(mm) || 0) * CSS_DPI / MM_PER_INCH;
  }

  function pxToMm(px) {
    return (Number(px) || 0) * MM_PER_INCH / CSS_DPI;
  }

  function paperWidthPx(viewportPx) {
    const value = Math.round(Number(viewportPx) || VIEWPORT_DEFAULT_PX);
    return Math.min(VIEWPORT_MAX_PX, Math.max(VIEWPORT_MIN_PX, value || VIEWPORT_DEFAULT_PX));
  }

  function paperWidthMm(viewportPx) {
    return pxToMm(paperWidthPx(viewportPx));
  }

  function contentWidthPx(viewportPx, marginLeftMm, marginRightMm) {
    return Math.max(40, paperWidthPx(viewportPx) - mmToPx(marginLeftMm) - mmToPx(marginRightMm));
  }

  function letterSpacingIsEm(value) {
    const n = Math.abs(Number(value) || 0);
    return n > 0 && n <= LETTER_SPACING_EM_ABS_MAX;
  }

  function letterSpacingCss(value) {
    const n = Number(value) || 0;
    if (!n) return "0";
    if (Math.abs(n) <= LETTER_SPACING_EM_ABS_MAX) return `${n}em`;
    return `${n}pt`;
  }

  function layoutMetrics(spec) {
    const src = spec || {};
    const viewport = paperWidthPx(src.mobile_viewport_px);
    const fontPt = Number(src.font_size_pt) || 10;
    const linePercent = Number(src.line_height_percent) || 150;
    const spacing = Number(src.letter_spacing_pt) || 0;
    const indentPt = Number(src.paragraph_indent_pt) || 0;
    const paraGapPt = Number(src.paragraph_spacing_pt) || 0;
    const padLeft = mmToPx(src.margin_left_mm);
    const padRight = mmToPx(src.margin_right_mm);
    const padTop = mmToPx(src.margin_top_mm);
    const padBottom = mmToPx(src.margin_bottom_mm);
    return {
      viewportPx: viewport,
      paperWidthMm: paperWidthMm(viewport),
      paperHeightMm: A4_HEIGHT_MM,
      contentWidthPx: Math.max(40, viewport - padLeft - padRight),
      padLeft,
      padRight,
      padTop,
      padBottom,
      fontSizePt: fontPt,
      fontSizePx: ptToPx(fontPt),
      fontSizeCss: `${fontPt}pt`,
      lineHeightPercent: linePercent,
      lineHeightCss: `${linePercent}%`,
      letterSpacingCss: letterSpacingCss(spacing),
      indentCss: `${indentPt}pt`,
      paraGapCss: `${paraGapPt}pt`,
    };
  }

  global.TypesetMetrics = {
    CSS_DPI,
    PT_PER_INCH,
    MM_PER_INCH,
    TWIPS_PER_PT,
    HWPUNIT_PER_MM,
    VIEWPORT_MIN_PX,
    VIEWPORT_MAX_PX,
    VIEWPORT_DEFAULT_PX,
    A4_HEIGHT_MM,
    LETTER_SPACING_EM_ABS_MAX,
    ptToPx,
    mmToPx,
    pxToMm,
    paperWidthPx,
    paperWidthMm,
    contentWidthPx,
    letterSpacingIsEm,
    letterSpacingCss,
    layoutMetrics,
  };
})(typeof window !== "undefined" ? window : globalThis);
