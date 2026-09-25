"""장편: 이전 단위 언급을 확인된 사실만 단정하고, 아니면 질문으로 바꾼다."""

from __future__ import annotations

import re
from typing import Any


# "5장면"은 단위 번호가 아니다. (?!면)으로 제외.
_UNIT_REF = re.compile(
    r"(?P<label>(?P<num>\d+)\s*장(?!면)|(?P<ord>\d+)\s*단위)"
)


def _known_unit_numbers(*blobs: str) -> set[int]:
    found: set[int] = set()
    joined = "\n".join(str(item or "") for item in blobs)
    for match in _UNIT_REF.finditer(joined):
        raw = match.group("num") or match.group("ord")
        if raw:
            found.add(int(raw))
    return found


def _evidence_blob(*parts: str) -> str:
    return "\n".join(str(part or "") for part in parts if str(part or "").strip())


def _claim_supported(claim: str, evidence: str) -> bool:
    """요약·설정집·이웃 원문에 같은 핵심 구가 있으면 지지된 것으로 본다(느슨한 검사)."""
    blob = evidence
    compact = re.sub(r"\s+", "", claim)
    if len(compact) < 6:
        return True
    tokens = re.findall(r"[가-힣A-Za-z]{2,}", claim)
    hits = 0
    for token in tokens[:8]:
        if token in blob:
            hits += 1
    return hits >= max(1, min(3, len(tokens) // 3))


def soften_unverified_past_claims(
    text: str,
    *,
    prior_context: str,
    settings_text: str = "",
    neighbor_text: str = "",
    current_unit_no: int = 0,
) -> tuple[str, list[dict[str, str]]]:
    """앞선 단위를 단정한 문장만 검증한다.

    - 현재 단위 번호(예: 3장 실행 중 '3장')와 장면 번호('5장면')는 검증하지 않는다.
    - 근거: 이전 요약·이 장이 한 일·설정집 + 직전/다음 이웃 장면 원문.
    """
    source = str(text or "")
    if not source.strip():
        return source, []
    evidence = _evidence_blob(prior_context, settings_text, neighbor_text)
    known = _known_unit_numbers(prior_context, settings_text, neighbor_text)
    current = int(current_unit_no or 0)
    # 직전 단위 원문이 있으면 그 단위 번호는 검증 근거가 있는 것으로 본다.
    if str(neighbor_text or "").strip() and current > 1:
        known.add(current - 1)
    log: list[dict[str, str]] = []
    parts = re.split(r"(?<=[.!?。…])\s+|\n+", source)
    out: list[str] = []
    for part in parts:
        chunk = part.strip()
        if not chunk:
            continue
        match = _UNIT_REF.search(chunk)
        if match is None:
            out.append(chunk)
            continue
        num = int(match.group("num") or match.group("ord") or 0)
        # 현재·이후 단위 언급은 과거 사실 검증 대상이 아님
        if current > 0 and num >= current:
            out.append(chunk)
            continue
        if num <= 0:
            out.append(chunk)
            continue
        if f"{num}장" not in chunk and f"{num}단위" not in chunk:
            chunk = f"{num}장: {chunk}"
        # 근거 텍스트가 주장을 지지하면 단정 유지(이웃 원문 포함)
        if _claim_supported(chunk, evidence):
            out.append(chunk)
            continue
        if num not in known:
            question = (
                f"{num}장에서 그렇게 나온 것 같은데, 맞다면 이 장과 어긋납니다. "
                f"(근거: {chunk[:120]})"
            )
            log.append(
                {
                    "action": "soften_past_claim",
                    "target": f"{num}장",
                    "detail": chunk[:200],
                }
            )
            out.append(question)
            continue
        question = (
            f"{num}장에서 그렇게 나온 것 같은데, 맞다면 이 장과 어긋납니다. "
            f"(확인 불가: {chunk[:120]})"
        )
        log.append(
            {
                "action": "soften_past_claim",
                "target": f"{num}장",
                "detail": chunk[:200],
            }
        )
        out.append(question)
    return "\n".join(out), log


def soften_report_past_facts(
    report: dict[str, Any],
    *,
    prior_context: str,
    settings_text: str = "",
    neighbor_text: str = "",
    current_unit_no: int = 0,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """리포트 텍스트 필드에 과거 사실 규칙을 적용한다."""
    log: list[dict[str, str]] = []
    updated = dict(report)

    def touch(value: object) -> str:
        text, items = soften_unverified_past_claims(
            str(value or ""),
            prior_context=prior_context,
            settings_text=settings_text,
            neighbor_text=neighbor_text,
            current_unit_no=current_unit_no,
        )
        log.extend(items)
        return text

    overview = dict(updated.get("overview") or {}) if isinstance(updated.get("overview"), dict) else {}
    for key in ("reader", "editor", "critic", "judge"):
        if overview.get(key):
            overview[key] = touch(overview.get(key))
    updated["overview"] = overview

    reading = dict(updated.get("reading") or {}) if isinstance(updated.get("reading"), dict) else {}
    for key in ("body", "intent_gap"):
        if reading.get(key):
            reading[key] = touch(reading.get(key))
    updated["reading"] = reading

    for field in ("diagnoses", "diagnoses_internal", "diagnoses_in_work"):
        items = []
        for raw in updated.get(field) or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if item.get("note"):
                item["note"] = touch(item.get("note"))
            items.append(item)
        if field in updated or items:
            updated[field] = items

    relation = dict(updated.get("settings_relation") or {}) if isinstance(updated.get("settings_relation"), dict) else {}
    if relation.get("conflicts"):
        cleaned = []
        for raw in relation.get("conflicts") or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if item.get("note"):
                item["note"] = touch(item.get("note"))
            cleaned.append(item)
        relation["conflicts"] = cleaned
    updated["settings_relation"] = relation
    return updated, log
