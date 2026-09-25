"""버전 비교를 문단 차이에 묶는다. 손대지 않은 문단의 판정 흔들림은 표시하지 않는다."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from feedback_pipeline.literature_text import loose_norm, quote_in_text


def snapshot_paragraphs(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """실행에 저장된 회차 문단을 작품 전체 순서로 다시 붙인다."""
    rows: list[dict[str, Any]] = []
    ordered = sorted(scenes or [], key=lambda scene: (int(scene.get("ord") or 0), int(scene.get("id") or 0)))
    for scene in ordered:
        offset = int(scene.get("para_offset") or 0)
        body = [
            item for item in (scene.get("paragraphs") or [])
            if str(item.get("type") or "") != "divider" and str(item.get("text") or "").strip()
        ]
        for step, item in enumerate(body, start=1):
            rows.append(
                {
                    "n": offset + step,
                    "text": str(item.get("text") or ""),
                    "scene_id": int(scene.get("scene_id") or 0),
                    "local": int(item.get("i") or step),
                }
            )
    return rows


def changed_indexes(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> set[int]:
    """현재 원고에서 바뀌었거나 새로 생긴 문단의 인덱스."""
    prev_norms = [loose_norm(str(item.get("text") or "")) for item in previous]
    curr_norms = [loose_norm(str(item.get("text") or "")) for item in current]
    matcher = SequenceMatcher(a=prev_norms, b=curr_norms, autojunk=False)
    changed: set[int] = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changed.update(range(j1, j2))
    return changed


def _quotes(item: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for raw in item.get("evidence") or []:
        if isinstance(raw, dict):
            quote = str(raw.get("quote") or "").strip()
            if quote:
                found.append(quote)
    quote = str(item.get("quote") or "").strip()
    if quote:
        found.append(quote)
    return found


def _quote_indexes(item: dict[str, Any], paragraphs: list[dict[str, Any]]) -> list[list[int]]:
    found = []
    for quote in _quotes(item):
        found.append([
            index for index, row in enumerate(paragraphs)
            if quote_in_text(quote, str(row.get("text") or ""))
        ])
    return found


def _only_changed(item: dict[str, Any], paragraphs: list[dict[str, Any]], changed: set[int]) -> bool:
    """근거가 바뀐 문단이나 새 문단에만 있을 때 참이다."""
    groups = _quote_indexes(item, paragraphs)
    if not groups:
        return False
    stable = False
    moved = False
    for matches in groups:
        if not matches or any(index in changed for index in matches):
            moved = True
        if matches and any(index not in changed for index in matches):
            stable = True
    return moved and not stable


def _evidence_shift(item: dict[str, Any], paragraphs: list[dict[str, Any]], changed: set[int]) -> str:
    """stable, partial, changed. partial은 근거 일부만 바뀐 경우다."""
    groups = _quote_indexes(item, paragraphs)
    if not groups:
        return "stable"
    stable = False
    moved = False
    for matches in groups:
        if not matches or any(index in changed for index in matches):
            moved = True
        if matches and any(index not in changed for index in matches):
            stable = True
    if moved and stable:
        return "partial"
    if moved:
        return "changed"
    return "stable"


def _by_key(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key and key not in found:
            found[key] = item
    return found


def stabilize_comparison(
    report: dict[str, Any],
    previous: dict[str, Any] | None,
    previous_paragraphs: list[dict[str, Any]],
    current_paragraphs: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """근거 문단이 그대로면 이전 판정을 유지한다."""
    log: list[dict[str, str]] = []
    cleaned = dict(report or {})
    if not previous:
        return cleaned, log
    changed = changed_indexes(previous_paragraphs, current_paragraphs)
    prev_items = _by_key(list(previous.get("diagnoses") or []))
    current_items = [item for item in (cleaned.get("diagnoses") or []) if isinstance(item, dict)]
    kept: list[dict[str, Any]] = []
    comparison: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in current_items:
        key = str(item.get("key") or "").strip()
        prior = prev_items.get(key)
        touches = _only_changed(item, current_paragraphs, changed)
        if prior is None:
            if touches:
                comparison.append(
                    {"key": key, "status": "new", "note": str(item.get("note") or "")}
                )
                kept.append(item)
            else:
                log.append(
                    {
                        "action": "drop_wobble",
                        "target": key or "diagnosis",
                        "detail": "근거 문단이 그대로라 새 진단을 비교에서 빼었습니다.",
                    }
                )
            continue
        seen.add(key)
        if not touches:
            shift = _evidence_shift(item, current_paragraphs, changed)
            if shift == "partial":
                log.append(
                    {
                        "action": "partial_evidence_held",
                        "target": key,
                        "detail": f"근거 일부만 바뀌어 판정을 다시 계산하지 않음 ({item.get('verdict')} -> {prior.get('verdict')})",
                    }
                )
            elif str(item.get("verdict") or "") != str(prior.get("verdict") or ""):
                log.append(
                    {
                        "action": "keep_verdict",
                        "target": key,
                        "detail": f"{item.get('verdict')} -> {prior.get('verdict')}",
                    }
                )
            if str(item.get("verdict") or "") != str(prior.get("verdict") or ""):
                item = {
                    **item,
                    "verdict": prior.get("verdict"),
                    "note": prior.get("note") or item.get("note"),
                    "intentional": prior.get("intentional"),
                }
            comparison.append({"key": key, "status": "same", "note": "근거 문단이 그대로입니다."})
            kept.append(item)
            continue
        verdict = str(item.get("verdict") or "")
        prior_verdict = str(prior.get("verdict") or "")
        if verdict == prior_verdict:
            status = "same"
        elif verdict == "works" and prior_verdict != "works":
            status = "resolved"
        elif verdict == "problem" and prior_verdict != "problem":
            status = "new"
        else:
            status = "partial"
        comparison.append({"key": key, "status": status, "note": str(item.get("note") or "")})
        kept.append(item)
    for key, prior in prev_items.items():
        if key in seen:
            continue
        if _only_changed(prior, current_paragraphs, changed):
            comparison.append({"key": key, "status": "resolved", "note": str(prior.get("note") or "")})
        else:
            comparison.append({"key": key, "status": "same", "note": "근거 문단이 그대로입니다."})
            kept.append(dict(prior))
            log.append(
                {
                    "action": "keep_verdict",
                    "target": key,
                    "detail": "이번 실행에서 빠졌지만 근거 문단이 그대로라 이전 판정을 유지합니다.",
                }
            )
    cleaned["diagnoses"] = kept
    reading = previous.get("reading") if isinstance(previous.get("reading"), dict) else {}
    current_reading = cleaned.get("reading") if isinstance(cleaned.get("reading"), dict) else {}
    cleaned["comparison"] = {
        "reading_changed": str(current_reading.get("body") or "") != str(reading.get("body") or ""),
        "items": comparison,
    }
    return cleaned, log
