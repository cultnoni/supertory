"""Server-side post-processing for report JSON. Pure functions, no API calls."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

LAB_DIR = Path(__file__).resolve().parent
ROOT = LAB_DIR.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(LAB_DIR) not in sys.path:
    sys.path.insert(0, str(LAB_DIR))

from manuscript import Paragraph  # noqa: E402
from report_checks import (  # noqa: E402
    MAX_RANGE_SPAN,
    _as_text,
    _list_of_dicts,
    _range_span,
    _spans_overlap,
    apply_impact_floor,
    check_r2b,
    check_r3,
    item_char_span,
)

P2_MARKERS = ("일치합니다", "일치함", "문제 없", "확인되지", "드러나지 않")

V2_RESULTS = LAB_DIR / "results" / "20260920-230043"


def _item_label(item: dict[str, Any]) -> str:
    iid = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    if iid and title:
        return f"{iid} {title}"
    return iid or title or "(제목 없음)"


def apply_postprocess(
    report: dict[str, Any] | None,
    paragraphs: list[Paragraph],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Apply P1–P4 in order. Returns (new_report, change_list)."""
    if not isinstance(report, dict):
        return None, [{"step": "skip", "reason": "리포트 JSON 없음"}]
    out = copy.deepcopy(report)
    changes: list[dict[str, Any]] = []

    weaknesses = _list_of_dicts(out.get("weaknesses"))
    for item in weaknesses:
        if _as_text(item.get("fixable")).strip() != "sentence":
            continue
        span = _range_span(item.get("range") if isinstance(item.get("range"), dict) else None)
        if span is not None and span > MAX_RANGE_SPAN:
            item["fixable"] = "structure"
            changes.append(
                {
                    "step": "P1",
                    "action": "fixable sentence→structure",
                    "item": _item_label(item),
                    "span": span,
                }
            )
    out["weaknesses"] = weaknesses

    kept_c: list[dict[str, Any]] = []
    for item in _list_of_dicts(out.get("consistency")):
        blob = f"{_as_text(item.get('title'))}\n{_as_text(item.get('body'))}"
        hit = next((m for m in P2_MARKERS if m in blob), None)
        if hit:
            changes.append(
                {
                    "step": "P2",
                    "action": "consistency 삭제",
                    "item": _item_label(item),
                    "marker": hit,
                }
            )
            continue
        kept_c.append(item)
    out["consistency"] = kept_c

    kept_w: list[dict[str, Any]] = []
    for w in _list_of_dicts(out.get("weaknesses")):
        w_span = item_char_span(w, paragraphs)
        overlapped = False
        overlap_with = ""
        if w_span is not None:
            for c in kept_c:
                c_span = item_char_span(c, paragraphs)
                if c_span is None:
                    continue
                if _spans_overlap(w_span, c_span):
                    overlapped = True
                    overlap_with = _item_label(c)
                    break
        if overlapped:
            changes.append(
                {
                    "step": "P3",
                    "action": "weakness 삭제 (consistency와 문자 구간 겹침)",
                    "item": _item_label(w),
                    "kept": overlap_with,
                }
            )
            continue
        kept_w.append(w)
    out["weaknesses"] = kept_w

    staged, floor_applied = apply_impact_floor(
        [{**it, "_group": "weaknesses"} for it in kept_w]
        + [{**it, "_group": "consistency"} for it in kept_c]
    )
    stage_by_id: dict[str, str] = {}
    for it in staged:
        iid = str(it.get("id") or "")
        if iid:
            stage_by_id[iid] = str(it.get("stage") or "")
        if it.get("_group") == "weaknesses":
            for w in kept_w:
                if w.get("id") == it.get("id") and w.get("title") == it.get("title"):
                    w["stage"] = it.get("stage")
                    if it.get("reference"):
                        w["reference"] = True
                    break
        elif it.get("_group") == "consistency":
            for c in kept_c:
                if c.get("id") == it.get("id") and c.get("title") == it.get("title"):
                    c["stage"] = it.get("stage")
                    if it.get("reference"):
                        c["reference"] = True
                    break
    out["weaknesses"] = kept_w
    out["consistency"] = kept_c
    changes.append(
        {
            "step": "P4",
            "action": "단계 부여",
            "floor_applied": floor_applied,
            "stages": {str(it.get("id") or _item_label(it)): it.get("stage") for it in staged},
        }
    )
    return out, changes


def _counts(report: dict[str, Any] | None) -> dict[str, int]:
    report = report or {}
    return {
        "strengths": len(_list_of_dicts(report.get("strengths"))),
        "weaknesses": len(_list_of_dicts(report.get("weaknesses"))),
        "consistency": len(_list_of_dicts(report.get("consistency"))),
        "impact_items": len(_list_of_dicts(report.get("weaknesses")))
        + len(_list_of_dicts(report.get("consistency"))),
    }


def summarize_effect(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    paragraphs: list[Paragraph],
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    r2b_before = check_r2b(before, paragraphs) if before else {"fail": 0}
    r2b_after = check_r2b(after, paragraphs) if after else {"fail": 0}
    r3_after = check_r3(after) if after else {}
    return {
        "before": _counts(before),
        "after": _counts(after),
        "r2b_fail_before": r2b_before.get("fail", 0),
        "r2b_fail_after": r2b_after.get("fail", 0),
        "r3_after": {
            "item_count": r3_after.get("item_count"),
            "counts": r3_after.get("counts"),
            "floor_applied": r3_after.get("floor_applied"),
        },
        "changes": changes,
    }


def render_post_effect(rows: list[dict[str, Any]]) -> str:
    parts = ["# 후처리 전후 비교 (P1–P4)", ""]
    parts.append("| 실행 | 항목 전 | 항목 후 | W 전/후 | C 전/후 | R2b 실패 전 | R2b 실패 후 | 변경 수 |")
    parts.append("|---|---|---|---|---|---|---|---|")
    for row in rows:
        b = row["before"]
        a = row["after"]
        parts.append(
            f"| {row['label']} "
            f"| {b['impact_items']} | {a['impact_items']} "
            f"| {b['weaknesses']}/{a['weaknesses']} "
            f"| {b['consistency']}/{a['consistency']} "
            f"| {row['r2b_fail_before']} | {row['r2b_fail_after']} "
            f"| {len(row['changes'])} |"
        )
    parts.append("")
    for row in rows:
        parts.append(f"## {row['label']}")
        if not row["changes"]:
            parts.append("- 변경 없음")
            parts.append("")
            continue
        for ch in row["changes"]:
            step = ch.get("step")
            if step == "P4":
                stages = ch.get("stages") or {}
                stage_txt = ", ".join(f"{k}={v}" for k, v in stages.items()) or "(없음)"
                floor = "바닥 보장 적용" if ch.get("floor_applied") else "바닥 보장 없음"
                parts.append(f"- P4 단계: {floor}; {stage_txt}")
                continue
            extra = ""
            if ch.get("span") is not None:
                extra = f" ({ch.get('span')}문단)"
            if ch.get("marker"):
                extra = f" (표지: {ch.get('marker')})"
            if ch.get("kept"):
                extra = f" → 남김: {ch.get('kept')}"
            parts.append(f"- {step} {ch.get('action')}: {ch.get('item')}{extra}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def load_manuscript_cache() -> dict[str, list[Paragraph]]:
    import json as _json
    import manuscript as ms

    cases = _json.loads((LAB_DIR / "cases.json").read_text(encoding="utf-8"))
    report_cases = _json.loads((LAB_DIR / "cases_report.json").read_text(encoding="utf-8"))
    metas = dict(cases.get("manuscripts") or {})
    for key, meta in (report_cases.get("extra_manuscripts") or {}).items():
        metas[key] = meta
    cache: dict[str, list[Paragraph]] = {}
    for key, meta in metas.items():
        filename = str((meta or {}).get("file") or "")
        path = LAB_DIR / "manuscripts" / filename
        if not path.is_file():
            continue
        cache[key] = ms.parse_paragraphs(path.read_text(encoding="utf-8"))
    return cache


def process_v2_folder(result_dir: Path, manuscript_cache: dict[str, list[Paragraph]]) -> str:
    payload = json.loads((result_dir / "results_report.json").read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for run in payload.get("runs") or []:
        if not isinstance(run, dict):
            continue
        if run.get("thinking") == "on" or run.get("error") or run.get("skipped"):
            continue
        parsed = run.get("parsed") if isinstance(run.get("parsed"), dict) else None
        mkey = str(run.get("manuscript") or "")
        paragraphs = manuscript_cache.get(mkey) or []
        after, changes = apply_postprocess(parsed, paragraphs)
        summary = summarize_effect(parsed, after, paragraphs, changes)
        summary["label"] = f"{run.get('id')}-{run.get('run_index')}"
        summary["id"] = run.get("id")
        rows.append(summary)
    return render_post_effect(rows)


def main(argv: list[str] | None = None) -> int:
    result_dir = V2_RESULTS
    if argv and argv[0]:
        result_dir = Path(argv[0])
    cache = load_manuscript_cache()
    md = process_v2_folder(result_dir, cache)
    out = result_dir / "post_effect.md"
    out.write_text(md, encoding="utf-8")
    print(f"저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
