"""리포트·정합성 후처리. P3(약점-정합성 겹침 삭제)는 이식하지 않는다."""

from __future__ import annotations

import copy
from typing import Any

from feedback_pipeline.checks import MAX_RANGE_SPAN, _as_int, _as_text, apply_card_priorities

P2_MARKERS = (
    "일치합니다",
    "일치함",
    "문제 없",
    "확인되지",
    "드러나지 않",
    "제외 대상",
    "확인용",
)


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _range_span(rng: Any) -> int | None:
    if not isinstance(rng, dict):
        return None
    start = _as_int(rng.get("start_para"))
    end = _as_int(rng.get("end_para"))
    if start is None or end is None:
        return None
    return abs(end - start) + 1


def _item_label(item: dict[str, Any]) -> str:
    iid = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    if iid and title:
        return f"{iid} {title}"
    return iid or title or "(제목 없음)"


def apply_p1_sentence_span(report: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """문장 항목이 5문단을 넘으면 structure로 바꾼다."""
    if not isinstance(report, dict):
        return None, []
    out = copy.deepcopy(report)
    changes: list[dict[str, Any]] = []
    weaknesses = _list_of_dicts(out.get("weaknesses"))
    for item in weaknesses:
        if _as_text(item.get("fixable")).strip() != "sentence":
            continue
        span = _range_span(item.get("range"))
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
    return out, changes


def is_p2_not_a_problem(item: dict[str, Any]) -> str | None:
    blob = f"{_as_text(item.get('title'))}\n{_as_text(item.get('body'))}"
    return next((m for m in P2_MARKERS if m in blob), None)


def filter_p2_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """정합성 항목에서 '문제 없음'류를 뺀다. 3-B issues와 리포트 consistency에 쓴다."""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        hit = is_p2_not_a_problem(item)
        if hit:
            dropped.append(
                {
                    "step": "P2",
                    "item": _item_label(item),
                    "marker": hit,
                    "id": str(item.get("id") or ""),
                    "title": str(item.get("title") or ""),
                }
            )
            continue
        kept.append(item)
    return kept, dropped


def apply_p2_report(report: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not isinstance(report, dict):
        return None, []
    out = copy.deepcopy(report)
    kept, dropped = filter_p2_items(_list_of_dicts(out.get("consistency")))
    out["consistency"] = kept
    return out, dropped


def apply_p4_stages(report: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """약점·정합성 항목에 단계를 붙인다(카드와 같은 바닥 보장)."""
    if not isinstance(report, dict):
        return None, []
    out = copy.deepcopy(report)
    items: list[dict[str, Any]] = []
    for group in ("weaknesses", "consistency"):
        for item in _list_of_dicts(out.get(group)):
            copy_item = dict(item)
            copy_item["_group"] = group
            items.append(copy_item)
    staged = apply_card_priorities(items)
    by_group: dict[str, list[dict[str, Any]]] = {"weaknesses": [], "consistency": []}
    for item in staged:
        group = str(item.pop("_group", "") or "")
        if group in by_group:
            by_group[group].append(item)
    out["weaknesses"] = by_group["weaknesses"]
    out["consistency"] = by_group["consistency"]
    return out, [{"step": "P4", "action": "단계 부여"}]


def apply_report_post(report: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """P1 → P2 → P4. P3는 폐기되어 넣지 않는다."""
    if not isinstance(report, dict):
        return None, [{"step": "skip", "reason": "리포트 JSON 없음"}]
    changes: list[dict[str, Any]] = []
    out, c1 = apply_p1_sentence_span(report)
    changes.extend(c1)
    out, c2 = apply_p2_report(out)
    changes.extend(c2)
    out, c4 = apply_p4_stages(out)
    changes.extend(c4)
    return out, changes
