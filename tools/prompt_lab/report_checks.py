"""Pure-function checks for report-stage lab (R1–R11). No I/O, no API calls."""

from __future__ import annotations

from typing import Any

from manuscript import Paragraph, _quote_end_original_index, find_in_paragraph

REQUIRED_KEYS = ("summary", "rules", "scores", "strengths", "weaknesses", "consistency")
ALLOWED_TYPES = frozenset(
    {
        "explain_less",
        "info_placement",
        "action_clarity",
        "redundancy",
        "reader_question",
        "abstract_expression",
        "other",
    }
)
ALLOWED_FIXABLE = frozenset({"sentence", "structure", "none"})
ALLOWED_PERSPECTIVES = frozenset({"editor", "reader", "critic"})
ALLOWED_CERTAINTY = frozenset({"sure", "maybe"})
MAX_RANGE_SPAN = 5
STRENGTH_SPAN_WARN = 3
DUP_SUMMARY_MARKERS = ("중복", "반복", "두 번")
LEAK_TERMS = ("tracked_facts", "impact", "fixable", "certainty", "range", "JSON")
NOT_A_PROBLEM_MARKERS = ("일치", "문제 없", "확인되지", "드러나지 않")


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        text = str(value or "").strip()
        if not text:
            return None
        return int(text)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _para_map(paragraphs: list[Paragraph]) -> dict[int, Paragraph]:
    return {p.number: p for p in paragraphs}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def coerce_report(parsed: Any) -> dict[str, Any] | None:
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                return item
    return None


def check_r1(parsed: Any) -> dict[str, Any]:
    report = coerce_report(parsed)
    missing: list[str] = []
    if report is None:
        return {
            "ok": False,
            "json_ok": False,
            "missing_keys": list(REQUIRED_KEYS),
        }
    for key in REQUIRED_KEYS:
        if key not in report:
            missing.append(key)
    return {
        "ok": not missing,
        "json_ok": True,
        "missing_keys": missing,
    }


def _range_para_numbers(range_obj: dict[str, Any] | None) -> list[int]:
    if not isinstance(range_obj, dict):
        return []
    start = _as_int(range_obj.get("start_para"))
    end = _as_int(range_obj.get("end_para"))
    if start is None or end is None:
        return []
    lo, hi = (start, end) if start <= end else (end, start)
    return list(range(lo, hi + 1))


def _start_para_of(item: dict[str, Any]) -> int:
    rng = item.get("range") if isinstance(item.get("range"), dict) else None
    if not rng:
        return 10**9
    n = _as_int(rng.get("start_para"))
    return n if n is not None else 10**9


def _range_span(rng: dict[str, Any] | None) -> int | None:
    if not isinstance(rng, dict):
        return None
    start = _as_int(rng.get("start_para"))
    end = _as_int(rng.get("end_para"))
    if start is None or end is None:
        return None
    return abs(end - start) + 1


def check_item_range_quotes(
    item: dict[str, Any],
    paragraphs: list[Paragraph],
) -> dict[str, Any]:
    """R2a: quotes actually sit in the named start/end paragraphs."""
    by_n = _para_map(paragraphs)
    rng = item.get("range")
    if rng is None:
        return {"ok": True, "null": True, "failures": []}
    if not isinstance(rng, dict):
        return {"ok": False, "null": False, "failures": ["range가 객체가 아님"]}

    failures: list[str] = []
    start = _as_int(rng.get("start_para"))
    end = _as_int(rng.get("end_para"))
    start_quote = _as_text(rng.get("start_quote")).strip()
    end_quote = _as_text(rng.get("end_quote")).strip()

    if start is None or end is None:
        failures.append("문단 번호 없음")
        return {"ok": False, "null": False, "failures": failures}

    first = by_n.get(start)
    last = by_n.get(end)
    if first is None:
        failures.append(f"start_para P{start} 없음")
    if last is None:
        failures.append(f"end_para P{end} 없음")
    if start > end:
        failures.append(f"start_para({start}) > end_para({end})")

    if first is not None and start_quote:
        if find_in_paragraph(first, start_quote) is None:
            failures.append("start_quote가 start_para에 없음")
    elif first is not None and not start_quote:
        failures.append("start_quote 비어 있음")

    if last is not None and end_quote:
        if find_in_paragraph(last, end_quote) is None:
            failures.append("end_quote가 end_para에 없음")
    elif last is not None and not end_quote:
        failures.append("end_quote 비어 있음")

    return {"ok": not failures, "null": False, "failures": failures}


def check_item_range_span(
    item: dict[str, Any],
    *,
    group: str,
) -> dict[str, Any]:
    """R2b: paragraph-span limits. structure skipped; strengths warn if > 3."""
    rng = item.get("range")
    if rng is None:
        return {"ok": True, "null": True, "fail": False, "warn": False, "skipped": False, "failures": []}
    if not isinstance(rng, dict):
        return {
            "ok": False,
            "null": False,
            "fail": True,
            "warn": False,
            "skipped": False,
            "failures": ["range가 객체가 아님"],
        }
    span = _range_span(rng)
    if span is None:
        return {
            "ok": False,
            "null": False,
            "fail": True,
            "warn": False,
            "skipped": False,
            "failures": ["문단 번호 없음"],
        }

    fixable = _as_text(item.get("fixable")).strip()
    if group == "weaknesses" and fixable == "structure":
        return {
            "ok": True,
            "null": False,
            "fail": False,
            "warn": False,
            "skipped": True,
            "failures": [],
            "span": span,
        }
    if group == "strengths":
        warn = span > STRENGTH_SPAN_WARN
        return {
            "ok": True,
            "null": False,
            "fail": False,
            "warn": warn,
            "skipped": False,
            "failures": ["구간 넓음"] if warn else [],
            "span": span,
        }
    apply = group == "consistency" or (group == "weaknesses" and fixable == "sentence")
    if not apply:
        return {
            "ok": True,
            "null": False,
            "fail": False,
            "warn": False,
            "skipped": True,
            "failures": [],
            "span": span,
        }
    too_long = span > MAX_RANGE_SPAN
    return {
        "ok": not too_long,
        "null": False,
        "fail": too_long,
        "warn": False,
        "skipped": False,
        "failures": [f"구간 {span}문단 (최대 {MAX_RANGE_SPAN})"] if too_long else [],
        "span": span,
    }


def _tally_range_group(
    report: dict[str, Any] | None,
    paragraphs: list[Paragraph],
    checker,
) -> dict[str, Any]:
    groups = {
        "strengths": _list_of_dicts((report or {}).get("strengths")),
        "weaknesses": _list_of_dicts((report or {}).get("weaknesses")),
        "consistency": _list_of_dicts((report or {}).get("consistency")),
    }
    by_group: dict[str, dict[str, int]] = {}
    pass_n = fail_n = null_n = warn_n = skip_n = 0
    details: list[dict[str, Any]] = []
    for name, items in groups.items():
        g_pass = g_fail = g_null = g_warn = g_skip = 0
        for item in items:
            result = checker(item, group=name, paragraphs=paragraphs)
            entry = {
                "group": name,
                "id": item.get("id"),
                "title": item.get("title"),
                **result,
            }
            details.append(entry)
            if result.get("null"):
                g_null += 1
                null_n += 1
            elif result.get("skipped"):
                g_skip += 1
                skip_n += 1
            elif result.get("fail") or (not result.get("ok") and not result.get("warn")):
                g_fail += 1
                fail_n += 1
            else:
                g_pass += 1
                pass_n += 1
            if result.get("warn"):
                g_warn += 1
                warn_n += 1
        by_group[name] = {
            "pass": g_pass,
            "fail": g_fail,
            "null": g_null,
            "warn": g_warn,
            "skipped": g_skip,
            "total": len(items),
        }
    return {
        "pass": pass_n,
        "fail": fail_n,
        "null": null_n,
        "warn": warn_n,
        "skipped": skip_n,
        "by_group": by_group,
        "details": details,
    }


def check_r2a(report: dict[str, Any] | None, paragraphs: list[Paragraph]) -> dict[str, Any]:
    def checker(item: dict[str, Any], *, group: str, paragraphs: list[Paragraph]) -> dict[str, Any]:
        return check_item_range_quotes(item, paragraphs)

    return _tally_range_group(report, paragraphs, checker)


def check_r2b(report: dict[str, Any] | None, paragraphs: list[Paragraph]) -> dict[str, Any]:
    def checker(item: dict[str, Any], *, group: str, paragraphs: list[Paragraph]) -> dict[str, Any]:
        return check_item_range_span(item, group=group)

    return _tally_range_group(report, paragraphs, checker)


def check_r2(report: dict[str, Any] | None, paragraphs: list[Paragraph]) -> dict[str, Any]:
    """Back-compat alias: quote checks only (R2a)."""
    return check_r2a(report, paragraphs)


def impact_stage(impact: int) -> str:
    if impact >= 4:
        return "high"
    if impact == 3:
        return "medium"
    return "low"


def _impact_items(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in ("weaknesses", "consistency"):
        for item in _list_of_dicts((report or {}).get(group)):
            copy = dict(item)
            copy["_group"] = group
            items.append(copy)
    return items


def apply_impact_floor(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Stage from impact. Promote only impact>=3. Impact<=2 stays low.

    If fewer than 3 highs remain, mark the top-3 by impact as reference.
    """
    tagged: list[dict[str, Any]] = []
    for item in items:
        copy = dict(item)
        impact = _as_int(copy.get("impact"))
        copy["_impact_valid"] = impact is not None and 1 <= impact <= 5
        copy["reference"] = False
        if not copy["_impact_valid"]:
            copy["stage"] = "low"
            tagged.append(copy)
            continue
        copy["stage"] = impact_stage(impact)
        tagged.append(copy)

    valid = [it for it in tagged if it.get("_impact_valid")]
    floor_applied = False
    if not valid:
        return tagged, False

    def _promo_key(it: dict[str, Any]) -> tuple[int, int]:
        impact = _as_int(it.get("impact")) or 0
        return (-impact, _start_para_of(it))

    highs = [it for it in valid if it.get("stage") == "high"]
    if len(highs) < 3:
        promotable = [
            it
            for it in valid
            if it.get("stage") != "high" and (_as_int(it.get("impact")) or 0) >= 3
        ]
        promotable.sort(key=_promo_key)
        need = 3 - len(highs)
        for it in promotable[:need]:
            it["stage"] = "high"
            floor_applied = True

    highs = [it for it in valid if it.get("stage") == "high"]
    if len(highs) < 3:
        ranked = sorted(valid, key=_promo_key)
        for it in ranked[:3]:
            it["reference"] = True
    return tagged, floor_applied


def check_r3(report: dict[str, Any] | None) -> dict[str, Any]:
    raw_items = _impact_items(report)
    tagged, floor_applied = apply_impact_floor(raw_items)
    valid_impacts = [it for it in tagged if it.get("_impact_valid")]
    invalid_impact = len(tagged) - len(valid_impacts)
    maybe_over = 0
    for it in tagged:
        certainty = _as_text(it.get("certainty")).strip().lower()
        impact = _as_int(it.get("impact"))
        if certainty == "maybe" and impact is not None and impact > 3:
            maybe_over += 1
    counts = {"high": 0, "medium": 0, "low": 0}
    for it in tagged:
        stage = str(it.get("stage") or "low")
        if stage in counts:
            counts[stage] += 1
    n = len(tagged)
    high_over_half = bool(n and counts["high"] > n / 2)
    reference_ids = [it.get("id") for it in tagged if it.get("reference")]
    return {
        "item_count": n,
        "invalid_impact": invalid_impact,
        "maybe_impact_over_3": maybe_over,
        "floor_applied": floor_applied,
        "high_over_half": high_over_half,
        "reference_count": len(reference_ids),
        "reference_ids": reference_ids,
        "counts": counts,
        "items": tagged,
    }


def _para_char_offsets(paragraphs: list[Paragraph]) -> dict[int, int]:
    offsets: dict[int, int] = {}
    pos = 0
    for para in paragraphs:
        offsets[para.number] = pos
        pos += len(para.text) + 1
    return offsets


def item_char_span(
    item: dict[str, Any],
    paragraphs: list[Paragraph],
) -> tuple[int, int] | None:
    rng = item.get("range") if isinstance(item.get("range"), dict) else None
    if not rng:
        return None
    start = _as_int(rng.get("start_para"))
    end = _as_int(rng.get("end_para"))
    start_quote = _as_text(rng.get("start_quote")).strip()
    end_quote = _as_text(rng.get("end_quote")).strip()
    if start is None or end is None or not start_quote or not end_quote:
        return None
    by_n = _para_map(paragraphs)
    first = by_n.get(start)
    last = by_n.get(end)
    if first is None or last is None:
        return None
    start_at = find_in_paragraph(first, start_quote)
    end_at = find_in_paragraph(last, end_quote)
    if start_at is None or end_at is None:
        return None
    end_end = _quote_end_original_index(last, end_quote, end_at)
    offsets = _para_char_offsets(paragraphs)
    global_start = offsets[start] + start_at
    global_end = offsets[end] + end_end
    if global_end < global_start:
        global_start, global_end = global_end, global_start
    return (global_start, global_end)


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return max(a[0], b[0]) < min(a[1], b[1])


def check_r4(
    report: dict[str, Any] | None,
    paragraphs: list[Paragraph],
    *,
    staged_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    strengths = _list_of_dicts((report or {}).get("strengths"))
    flagged = staged_items if staged_items is not None else _impact_items(report)
    conflicts: list[dict[str, Any]] = []
    for s in strengths:
        s_span = item_char_span(s, paragraphs)
        if s_span is None:
            continue
        for item in flagged:
            i_span = item_char_span(item, paragraphs)
            if i_span is None:
                continue
            if _spans_overlap(s_span, i_span):
                conflicts.append(
                    {
                        "strength_id": s.get("id"),
                        "strength_title": s.get("title"),
                        "other_id": item.get("id"),
                        "other_title": item.get("title"),
                        "other_group": item.get("_group"),
                        "overlap_chars": [max(s_span[0], i_span[0]), min(s_span[1], i_span[1])],
                    }
                )
    return {"count": len(conflicts), "conflicts": conflicts}


def check_r5(report: dict[str, Any] | None) -> dict[str, Any]:
    weaknesses = _list_of_dicts((report or {}).get("weaknesses"))
    bad_type = 0
    bad_fixable = 0
    types: list[str] = []
    fixables: list[str] = []
    for item in weaknesses:
        t = _as_text(item.get("type")).strip()
        types.append(t)
        if t not in ALLOWED_TYPES:
            bad_type += 1
        f = _as_text(item.get("fixable")).strip()
        fixables.append(f)
        if f not in ALLOWED_FIXABLE:
            bad_fixable += 1

    bad_persp = 0
    persp_values: list[str] = []
    for group in ("strengths", "weaknesses", "consistency"):
        for item in _list_of_dicts((report or {}).get(group)):
            persp = item.get("perspectives")
            if not isinstance(persp, list):
                bad_persp += 1
                continue
            for tag in persp:
                text = _as_text(tag).strip()
                persp_values.append(text)
                if text not in ALLOWED_PERSPECTIVES:
                    bad_persp += 1

    scores = _list_of_dicts((report or {}).get("scores"))
    score_count = len(scores)
    scores_ok = 4 <= score_count <= 5
    lowest_name = ""
    lowest_score = None
    for row in scores:
        sc = _as_int(row.get("score"))
        name = _as_text(row.get("item")).strip()
        if sc is None:
            continue
        if lowest_score is None or sc < lowest_score:
            lowest_score = sc
            lowest_name = name
    return {
        "bad_type": bad_type,
        "bad_fixable": bad_fixable,
        "bad_perspectives": bad_persp,
        "types": types,
        "fixables": fixables,
        "score_count": score_count,
        "scores_ok": scores_ok,
        "lowest_score_item": lowest_name,
        "lowest_score": lowest_score,
    }


def _item_text(item: dict[str, Any]) -> str:
    return f"{_as_text(item.get('title'))}\n{_as_text(item.get('body'))}"


def check_r6(
    report: dict[str, Any] | None,
    paragraphs: list[Paragraph],
    planted: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if not planted:
        return None
    flagged: list[dict[str, Any]] = []
    for group in ("weaknesses", "consistency"):
        for item in _list_of_dicts((report or {}).get(group)):
            copy = dict(item)
            copy["_group"] = group
            flagged.append(copy)
    strengths = _list_of_dicts((report or {}).get("strengths"))

    rows: list[dict[str, Any]] = []
    detected_n = 0
    required_n = 0
    required_hit = 0
    in_strength_n = 0
    for plant in planted:
        quote = _as_text(plant.get("quote")).strip()
        required = bool(plant.get("required"))
        paras_hit: list[int] = []
        for para in paragraphs:
            if quote and find_in_paragraph(para, quote) is not None:
                paras_hit.append(para.number)
        detected = False
        matched: list[str] = []
        for item in flagged:
            rng = item.get("range") if isinstance(item.get("range"), dict) else None
            covered = set(_range_para_numbers(rng))
            if paras_hit and any(n in covered for n in paras_hit):
                detected = True
                matched.append(str(item.get("id") or item.get("title") or ""))
                continue
            blob = _item_text(item)
            if quote and quote in blob:
                detected = True
                matched.append(str(item.get("id") or item.get("title") or ""))
        in_strength = False
        strength_ids: list[str] = []
        for s in strengths:
            rng = s.get("range") if isinstance(s.get("range"), dict) else None
            covered = set(_range_para_numbers(rng))
            if paras_hit and any(n in covered for n in paras_hit):
                in_strength = True
                strength_ids.append(str(s.get("id") or s.get("title") or ""))
        if in_strength:
            in_strength_n += 1
        if detected:
            detected_n += 1
        if required:
            required_n += 1
            if detected:
                required_hit += 1
        rows.append(
            {
                "id": plant.get("id"),
                "kind": plant.get("kind"),
                "quote": quote,
                "required": required,
                "paragraphs": paras_hit,
                "detected": detected,
                "matched_items": matched,
                "in_strength_range": in_strength,
                "strength_ids": strength_ids,
            }
        )
    total = len(planted)
    return {
        "total": total,
        "detected": detected_n,
        "rate": (detected_n / total) if total else 0.0,
        "required_total": required_n,
        "required_detected": required_hit,
        "required_missed": [
            row["id"] for row in rows if row["required"] and not row["detected"]
        ],
        "in_strength_count": in_strength_n,
        "rows": rows,
    }


def check_r7(
    *,
    expect_duplicates: bool,
    dup_blocks: list[dict[str, list[int]]],
    report: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not expect_duplicates:
        return None
    summary = _as_text((report or {}).get("summary"))
    has_keyword = any(mark in summary for mark in DUP_SUMMARY_MARKERS)
    structure_impact5 = False
    for item in _list_of_dicts((report or {}).get("weaknesses")):
        fixable = _as_text(item.get("fixable")).strip()
        impact = _as_int(item.get("impact"))
        if fixable == "structure" and impact == 5:
            structure_impact5 = True
            break
    blocks_ok = bool(dup_blocks)
    return {
        "dup_blocks_found": blocks_ok,
        "dup_block_count": len(dup_blocks),
        "structure_impact5": structure_impact5,
        "summary_keyword": has_keyword,
        "ok": blocks_ok and structure_impact5 and has_keyword,
    }


def high_para_set(staged_items: list[dict[str, Any]]) -> set[int]:
    nums: set[int] = set()
    for item in staged_items:
        if item.get("stage") != "high":
            continue
        rng = item.get("range") if isinstance(item.get("range"), dict) else None
        nums.update(_range_para_numbers(rng))
    return nums


def jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def check_r8(repeat_runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """repeat_runs: [{id, run_index, high_paras: set|list, high_count, item_count}, ...]"""
    if len(repeat_runs) < 2:
        return None
    rows = []
    for run in repeat_runs:
        rows.append(
            {
                "id": run.get("id"),
                "run_index": run.get("run_index"),
                "high_count": int(run.get("high_count") or 0),
                "item_count": int(run.get("item_count") or 0),
                "high_paras": sorted(set(run.get("high_paras") or [])),
            }
        )
    pairs: list[dict[str, Any]] = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a = set(rows[i]["high_paras"])
            b = set(rows[j]["high_paras"])
            pairs.append(
                {
                    "a": f"{rows[i]['id']}-{rows[i]['run_index']}",
                    "b": f"{rows[j]['id']}-{rows[j]['run_index']}",
                    "jaccard": round(jaccard(a, b), 4),
                    "intersection": len(a & b),
                    "union": len(a | b),
                }
            )
    return {"runs": rows, "pairs": pairs}


def _range_key(item: dict[str, Any]) -> tuple[Any, ...] | None:
    rng = item.get("range") if isinstance(item.get("range"), dict) else None
    if not rng:
        return None
    start = _as_int(rng.get("start_para"))
    end = _as_int(rng.get("end_para"))
    if start is None or end is None:
        return None
    return (
        start,
        end,
        _as_text(rng.get("start_quote")).strip(),
        _as_text(rng.get("end_quote")).strip(),
    )


def _count_term(text: str, term: str) -> int:
    if not text or not term:
        return 0
    if term.lower() == "json":
        hay = text
        needle = term
        count = 0
        start = 0
        low = hay.lower()
        nlow = needle.lower()
        while True:
            pos = low.find(nlow, start)
            if pos < 0:
                break
            count += 1
            start = pos + len(nlow)
        return count
    count = 0
    start = 0
    while True:
        pos = text.find(term, start)
        if pos < 0:
            break
        count += 1
        start = pos + len(term)
    return count


def _report_body_blobs(report: dict[str, Any] | None) -> list[str]:
    blobs: list[str] = [_as_text((report or {}).get("summary"))]
    for row in _list_of_dicts((report or {}).get("scores")):
        blobs.append(_as_text(row.get("item")))
        blobs.append(_as_text(row.get("comment")))
    for group in ("strengths", "weaknesses", "consistency"):
        for item in _list_of_dicts((report or {}).get(group)):
            blobs.append(_as_text(item.get("title")))
            blobs.append(_as_text(item.get("body")))
    return blobs


def check_r9(report: dict[str, Any] | None) -> dict[str, Any]:
    weaknesses = _list_of_dicts((report or {}).get("weaknesses"))
    consistency = _list_of_dicts((report or {}).get("consistency"))
    pairs: list[dict[str, Any]] = []
    for w in weaknesses:
        wkey = _range_key(w)
        if wkey is None:
            continue
        for c in consistency:
            if _range_key(c) == wkey:
                pairs.append(
                    {
                        "weakness_id": w.get("id"),
                        "consistency_id": c.get("id"),
                        "range": {
                            "start_para": wkey[0],
                            "end_para": wkey[1],
                            "start_quote": wkey[2],
                            "end_quote": wkey[3],
                        },
                    }
                )
    by_term: dict[str, int] = {}
    total_leaks = 0
    blobs = _report_body_blobs(report)
    joined = "\n".join(blobs)
    for term in LEAK_TERMS:
        n = _count_term(joined, term)
        by_term[term] = n
        total_leaks += n
    return {
        "same_range_pairs": len(pairs),
        "pairs": pairs,
        "leak_count": total_leaks,
        "leaks": by_term,
    }


def check_r10(report: dict[str, Any] | None) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    for item in _list_of_dicts((report or {}).get("consistency")):
        blob = _item_text(item)
        found = [mark for mark in NOT_A_PROBLEM_MARKERS if mark in blob]
        if found:
            hits.append({"id": item.get("id"), "title": item.get("title"), "markers": found})
    return {"count": len(hits), "items": hits}


def check_r11(report: dict[str, Any] | None) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    counts = {"fixable": 0, "certainty": 0, "type": 0, "perspectives": 0}
    for item in _list_of_dicts((report or {}).get("weaknesses")):
        t = _as_text(item.get("type")).strip()
        if t not in ALLOWED_TYPES:
            counts["type"] += 1
            errors.append({"id": item.get("id"), "field": "type", "value": t})
        f = _as_text(item.get("fixable")).strip()
        if f not in ALLOWED_FIXABLE:
            counts["fixable"] += 1
            errors.append({"id": item.get("id"), "field": "fixable", "value": f})
    for group in ("strengths", "weaknesses", "consistency"):
        for item in _list_of_dicts((report or {}).get(group)):
            persp = item.get("perspectives")
            if not isinstance(persp, list):
                counts["perspectives"] += 1
                errors.append({"id": item.get("id"), "field": "perspectives", "value": persp})
                continue
            for tag in persp:
                text = _as_text(tag).strip()
                if text not in ALLOWED_PERSPECTIVES:
                    counts["perspectives"] += 1
                    errors.append({"id": item.get("id"), "field": "perspectives", "value": text})
            if "certainty" in item:
                cert = _as_text(item.get("certainty")).strip()
                if cert not in ALLOWED_CERTAINTY:
                    counts["certainty"] += 1
                    errors.append({"id": item.get("id"), "field": "certainty", "value": cert})
    return {"count": sum(counts.values()), "by_field": counts, "errors": errors}


def planted_paragraphs(paragraphs: list[Paragraph], quote: str) -> list[int]:
    hits: list[int] = []
    needle = _as_text(quote).strip()
    if not needle:
        return hits
    for para in paragraphs:
        if find_in_paragraph(para, needle) is not None:
            hits.append(para.number)
    return hits


def issue_covers_para(issue: dict[str, Any], para_n: int) -> bool:
    rng = issue.get("range") if isinstance(issue.get("range"), dict) else None
    if rng:
        nums = set(_range_para_numbers(rng))
        if para_n in nums:
            return True
    for ev in issue.get("evidence") or []:
        if not isinstance(ev, dict):
            continue
        n = _as_int(ev.get("para"))
        if n == para_n:
            return True
    return False


def check_consistency_output(
    parsed: Any,
    paragraphs: list[Paragraph],
    planted: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report = coerce_report(parsed)
    facts = _list_of_dicts((report or {}).get("facts")) if report else []
    issues = _list_of_dicts((report or {}).get("issues")) if report else []
    json_ok = isinstance(report, dict) and "facts" in (report or {}) and "issues" in (report or {})
    r2a_fail = 0
    r2a_pass = 0
    r2a_details: list[dict[str, Any]] = []
    for issue in issues:
        result = check_item_range_quotes(issue, paragraphs)
        r2a_details.append({"title": issue.get("title"), **result})
        if result.get("null"):
            continue
        if result.get("ok"):
            r2a_pass += 1
        else:
            r2a_fail += 1
    planted_rows = None
    if planted:
        planted_rows = []
        for plant in planted:
            quote = _as_text(plant.get("quote")).strip()
            paras = planted_paragraphs(paragraphs, quote)
            detected = False
            matched: list[str] = []
            for issue in issues:
                if any(issue_covers_para(issue, n) for n in paras):
                    detected = True
                    matched.append(str(issue.get("title") or ""))
                    continue
                blob = f"{_as_text(issue.get('title'))}\n{_as_text(issue.get('body'))}"
                if quote and quote in blob:
                    detected = True
                    matched.append(str(issue.get("title") or ""))
            planted_rows.append(
                {
                    "id": plant.get("id"),
                    "kind": plant.get("kind"),
                    "quote": quote,
                    "required": bool(plant.get("required")),
                    "paragraphs": paras,
                    "detected": detected,
                    "matched": matched,
                }
            )
    return {
        "json_ok": json_ok,
        "fact_count": len(facts),
        "issue_count": len(issues),
        "issues": [
            {
                "title": it.get("title"),
                "body": it.get("body"),
                "certainty": it.get("certainty"),
                "range": it.get("range"),
                "evidence": it.get("evidence"),
            }
            for it in issues
        ],
        "r2a_pass": r2a_pass,
        "r2a_fail": r2a_fail,
        "r2a_details": r2a_details,
        "planted": planted_rows,
    }


def run_all_checks(
    *,
    parsed: Any,
    paragraphs: list[Paragraph],
    planted: list[dict[str, Any]] | None = None,
    expect_duplicates: bool = False,
    dup_blocks: list[dict[str, list[int]]] | None = None,
) -> dict[str, Any]:
    r1 = check_r1(parsed)
    report = coerce_report(parsed) if r1.get("json_ok") else None
    empty_range = {"pass": 0, "fail": 0, "null": 0, "warn": 0, "skipped": 0, "by_group": {}, "details": []}
    r2a = check_r2a(report, paragraphs) if report is not None else dict(empty_range)
    r2b = check_r2b(report, paragraphs) if report is not None else dict(empty_range)
    r3 = check_r3(report) if report is not None else {
        "item_count": 0,
        "invalid_impact": 0,
        "maybe_impact_over_3": 0,
        "floor_applied": False,
        "high_over_half": False,
        "reference_count": 0,
        "reference_ids": [],
        "counts": {"high": 0, "medium": 0, "low": 0},
        "items": [],
    }
    r4 = (
        check_r4(report, paragraphs, staged_items=r3.get("items"))
        if report is not None
        else {"count": 0, "conflicts": []}
    )
    r5 = check_r5(report) if report is not None else {
        "bad_type": 0,
        "bad_fixable": 0,
        "bad_perspectives": 0,
        "types": [],
        "fixables": [],
        "score_count": 0,
        "scores_ok": False,
        "lowest_score_item": "",
        "lowest_score": None,
    }
    r6 = check_r6(report, paragraphs, planted)
    r7 = check_r7(
        expect_duplicates=expect_duplicates,
        dup_blocks=dup_blocks or [],
        report=report,
    )
    r9 = check_r9(report) if report is not None else {
        "same_range_pairs": 0,
        "pairs": [],
        "leak_count": 0,
        "leaks": {term: 0 for term in LEAK_TERMS},
    }
    r10 = check_r10(report) if report is not None else {"count": 0, "items": []}
    r11 = check_r11(report) if report is not None else {
        "count": 0,
        "by_field": {"fixable": 0, "certainty": 0, "type": 0, "perspectives": 0},
        "errors": [],
    }
    return {
        "r1": r1,
        "r2": r2a,
        "r2a": r2a,
        "r2b": r2b,
        "r3": r3,
        "r4": r4,
        "r5": r5,
        "r6": r6,
        "r7": r7,
        "r9": r9,
        "r10": r10,
        "r11": r11,
    }
