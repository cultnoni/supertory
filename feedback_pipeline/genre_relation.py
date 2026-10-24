"""관계형(로맨스) 장르 레이어.

수수께끼형·세계형과 같은 방식으로 웹소설 러너 안에서 두 번 부른다.
1) 관계 지도(관계와 감정이 어디서 어떻게 움직이는지)  2) 장르 진단 루브릭.
판단 기준은 기존 로맨스 기준집(data/genre_playbooks.json의 ``romance_modern``)과
작품 설정의 로맨스 축(정서형/사건형, 현대/사극)을 쓴다. 장르문학 로맨스는
``main_genre=romance``, ``sub_genre``가 비어 있어서 AI 패널의 기준집 찾기(``romance_``)에는
걸리지 않았다. 여기서는 축으로 직접 고른다.
두 묶음이 함께 쓰는 도구는 ``genre_common``에 있다.

설계는 docs/genre-pipeline.md의 「관계형」을 따른다.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import genre_track
from feedback_pipeline import genre_common
from feedback_pipeline.genre_common import LayerSpec, quote_in_manuscript
from feedback_pipeline.paragraphs import Paragraph

GROUP = genre_track.GROUP_RELATION

RUBRIC_KEYS = (
    "relationship_arc",
    "emotion_line",
    "conflict_drive",
    "pacing",
    "kept_promise",
)

MAX_EVENTS = 10
MAX_LIST = 8
_BOOK_FIELD_LIMIT = 420

_text = genre_common.text
_int = genre_common.to_int

_STRUCTURE_EMPHASIS = {
    "emotional": "정서형으로 읽는다. 감정선(emotion_line)과 관계의 전개(relationship_arc)를 더 무겁게 본다.",
    "plot_driven": "사건형으로 읽는다. 갈등의 동력(conflict_drive)과 사건 속에서 관계가 변하는 방식을 더 무겁게 본다.",
}
_PERIOD_EMPHASIS = (
    "사극 배경으로 읽는다. 호칭·말투·신분 관계가 설정집과 어긋나지 않는지 본다. "
    "역사 고증이 맞는지는 판정하지 않고, 필요하면 note에 '참고 의견:'으로 짧게만 쓴다."
)


def required_keys(has_kept_promise: bool) -> list[str]:
    keys = ["relationship_arc", "emotion_line", "conflict_drive", "pacing"]
    if has_kept_promise:
        keys.append("kept_promise")
    return keys


def _clip(value: object) -> str:
    return _text(value, _BOOK_FIELD_LIMIT)


def _playbook_text(project: dict[str, Any]) -> str:
    """기존 로맨스 기준집에서 판정에 필요한 부분만 뽑는다(없으면 빈 문자열)."""
    try:
        import app
    except ImportError:  # CLI 등 앱 없이 쓸 때
        return ""
    book = app.load_genre_playbook("romance", "modern")
    if not isinstance(book, dict):
        return ""
    rules = book.get("group_rules") if isinstance(book.get("group_rules"), dict) else {}
    lines = ["[로맨스 기준집]"]
    for label, value in (
        ("톤", book.get("tone")),
        ("점검", book.get("checklist")),
        ("피할 것", book.get("avoid")),
        ("판정 지침", rules.get("A_judge")),
    ):
        if value:
            lines.append(f"- {label}: {_clip(value)}")
    if str(project.get("romance_setting") or "").strip() == "period":
        delta = app.load_genre_playbook_delta("romance", "period", "")  # romance_modern__historical
        if isinstance(delta, dict):
            addition = delta.get("checklist_addition") or delta.get("checklist")
            if addition:
                lines.append(f"- 사극 추가 점검: {_clip(addition)}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _emphasis_text(project: dict[str, Any]) -> str:
    parts = [_STRUCTURE_EMPHASIS.get(str(project.get("romance_structure") or "").strip(), "")]
    if str(project.get("romance_setting") or "").strip() == "period":
        parts.append(_PERIOD_EMPHASIS)
    parts.append(_playbook_text(project))
    return "\n".join(part for part in parts if part)


def normalize_relation_map(raw: Any, paras: dict[int, Paragraph]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """관계 지도를 정리한다. 원고에 없는 인용이 붙은 사건은 버리고 목록을 돌려준다."""
    data = raw if isinstance(raw, dict) else {}
    dropped: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for item in data.get("events") or []:
        if not isinstance(item, dict):
            continue
        para = _int(item.get("para"))
        quote = _text(item.get("quote"))
        if para not in paras:
            dropped.append({"where": "relation_map.events", "reason": "para_invalid", "para": para})
            continue
        if quote and not quote_in_manuscript(paras, para, quote):
            dropped.append({"where": "relation_map.events", "reason": "quote_invalid", "para": para})
            continue
        kind = str(item.get("kind") or "other")
        if kind not in {"closer", "distance", "conflict", "show", "tell", "beat", "other"}:
            kind = "other"
        events.append({"para": para, "kind": kind, "what": _text(item.get("what"), 80), "quote": quote})
    events.sort(key=lambda row: row["para"])
    pairs = []
    for item in data.get("pairs") or []:
        if isinstance(item, dict) and _text(item.get("who")):
            pairs.append(
                {
                    "who": _text(item.get("who"), 40),
                    "at_start": _text(item.get("at_start"), 80),
                    "at_end": _text(item.get("at_end"), 80),
                }
            )
    obstacles = [
        s for s in (_text(v, 100) for v in (data.get("obstacles") or []) if isinstance(v, str)) if s
    ][:MAX_LIST]
    return (
        {
            "events": events[:MAX_EVENTS],
            "pairs": pairs[:MAX_LIST],
            "obstacles": obstacles,
            "ends_on": _text(data.get("ends_on"), 120),
        },
        dropped,
    )


def normalize_rubric(
    raw: Any,
    paras: dict[int, Paragraph],
    *,
    needed: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return genre_common.normalize_rubric(raw, paras, needed=needed, valid_keys=RUBRIC_KEYS)


SPEC = LayerSpec(
    group=GROUP,
    system_file="genre/relation_system.txt",
    map_prompt_file="genre/relation_map_prompt.txt",
    map_schema_file="genre/relation_map_schema.json",
    rubric_prompt_file="genre/relation_rubric_prompt.txt",
    rubric_schema_file="genre/relation_rubric_schema.json",
    map_label="관계 지도",
    map_key="relation_map",
    usage_stage="genre_relation_map",
    normalize_map=normalize_relation_map,
    valid_keys=RUBRIC_KEYS,
    required_keys=lambda project, has_kept: required_keys(has_kept),
    emphasis=_emphasis_text,
)


def run_relation_report(conn: sqlite3.Connection, project_id: int, paragraphs: list[Paragraph], **kwargs: Any) -> dict[str, Any]:
    return genre_common.run_report(SPEC, conn, project_id, paragraphs, **kwargs)


# ── 카드와 검증 ───────────────────────────────────────────────

# 관계·감정에 닿는 문제라 모두 지적형(수정안 없음)이다. 호칭·말투 불일치 수정안은
# 웹소설 러너의 정합성 카드가 설정집과 대조해서 만든다.
CARD_TAGS = {
    "relationship_arc": "arc_leap",
    "emotion_line": "emotion_telling",
    "conflict_drive": "conflict_weak",
    "pacing": "pacing_off",
    "kept_promise": "promise_gap",
}
CARD_LABELS = {
    "arc_leap": "관계 전개 비약",
    "emotion_telling": "감정 설명 과잉",
    "conflict_weak": "갈등 동력 약함",
    "pacing_off": "호흡 어긋남",
    "promise_gap": "지키고 싶은 약속",
}
_CORE_KEYS = {"relationship_arc", "emotion_line"}
PROTECTED_KINDS = {"closer", "distance", "conflict"}

RELATION_TOUCH_WARNING = {
    "code": "relation_touch",
    "severity": "info",
    "message": "관계가 움직이는 장면이라 수정안 대신 지적만 드려요",
}
TOUCH_WARNING = RELATION_TOUCH_WARNING


def protected_paragraphs(genre: dict[str, Any] | None) -> set[int]:
    """관계 지도에서 관계가 가까워지거나 멀어지거나 갈등이 생기는 문단 번호."""
    out: set[int] = set()
    relation_map = (genre or {}).get("relation_map") if isinstance(genre, dict) else None
    if not isinstance(relation_map, dict):
        return out
    for event in relation_map.get("events") or []:
        if isinstance(event, dict) and event.get("kind") in PROTECTED_KINDS:
            out.add(_int(event.get("para")))
    out.discard(0)
    return out


def build_cards(
    genre: dict[str, Any] | None,
    paragraphs: list[Paragraph],
    scene_id: int,
    *,
    promise_broken: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return genre_common.build_cards(
        genre,
        paragraphs,
        scene_id,
        promise_broken=promise_broken,
        tags=CARD_TAGS,
        labels=CARD_LABELS,
        core_keys=_CORE_KEYS,
    )


MD_TITLE = "장르 진단 (관계형)"
MD_LABELS = {
    "relationship_arc": "관계의 전개",
    "emotion_line": "감정선",
    "conflict_drive": "갈등의 동력",
    "pacing": "호흡",
    "kept_promise": "지키고 싶은 약속",
}
