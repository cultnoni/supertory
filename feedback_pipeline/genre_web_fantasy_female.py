"""여성향판타지형(웹소설) 장르 레이어.

로맨스형·무협판타지형과 같은 방식으로 웹소설 러너 안에서 두 번 부른다.
1) 서사 지도(복수·성장·관계·반전이 어디서 어떻게 움직이는지)  2) 장르 진단 루브릭.
여성향 판타지 하나만 보고, 하위 장르(차원이동·현대·시대물)는 ``sub_genre``로 가른다.
기존 기준집(data/genre_playbooks.json의 fantasy_female + delta)을 쓴다.

실제 저장값은 main_genre="female_fantasy", sub_genre=dimension/modern/period_east/period_west,
genre_detail은 비어 있다(``genre_clusters.playbook_lookup_keys``가 sub를 delta 키로 바꿔 준다).

설계는 docs/webnovel-genre-pipeline.md의 「여성향판타지형」을 따른다.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import webnovel_genre_track
from feedback_pipeline import genre_common
from feedback_pipeline.genre_common import LayerSpec, quote_in_manuscript
from feedback_pipeline.paragraphs import Paragraph

GROUP = webnovel_genre_track.GROUP_FANTASY_FEMALE

RUBRIC_KEYS = (
    "narrative_balance",
    "payoff_timing",
    "emotion_line",
    "world_status",
    "status_order",
    "world_split",
    "kept_avoid",
)

_BASE_KEYS = ["narrative_balance", "payoff_timing", "emotion_line", "world_status"]
# 설계의 「장르별 추가」. 현대(modern)는 추가 항목 없이 기준집 delta만 넣는다.
_EXTRA_BY_SUB = {
    "dimension": "world_split",
    "period_east": "status_order",
    "period_west": "status_order",
}
_EMPHASIS_BY_SUB = {
    "dimension": "차원이동으로 읽는다. 원래 세계와 이동한 세계의 규칙 구분(world_split)을 더 무겁게 본다.",
    "modern": "현대 여성향 판타지로 읽는다. 현실 사회 안에서 여주가 스스로 위기를 해결하는지를 더 무겁게 본다.",
    "period_east": "동양 시대물로 읽는다. 사극 설정과 신분 질서(status_order)를 더 무겁게 본다.",
    "period_west": "서양 시대물로 읽는다. 사극 설정과 신분 질서(status_order)를 더 무겁게 본다.",
}
_SUB_LABELS = {
    "dimension": "차원이동",
    "modern": "현대",
    "period_east": "시대물(동양)",
    "period_west": "시대물(서양)",
}

MAX_EVENTS = 10
MAX_LIST = 8

_text = genre_common.text
_int = genre_common.to_int


def _effective_sub(project: dict[str, Any]) -> str:
    return str(project.get("sub_genre") or "").strip()


def required_keys(project: dict[str, Any], *, has_kept_avoid: bool) -> list[str]:
    keys = list(_BASE_KEYS)
    extra = _EXTRA_BY_SUB.get(_effective_sub(project))
    if extra:
        keys.append(extra)
    if has_kept_avoid:
        keys.append("kept_avoid")
    return keys


def _playbook_text(project: dict[str, Any]) -> str:
    """기존 여성향 판타지 기준집에서 판정에 필요한 부분만 뽑는다. 도우미 A그룹과 같은 범위."""
    return genre_common.playbook_emphasis_text(
        project, heading="[여성향 판타지 기준집]", detail_labels=_SUB_LABELS
    )


def _emphasis_text(project: dict[str, Any]) -> str:
    parts = [_EMPHASIS_BY_SUB.get(_effective_sub(project), "")]
    parts.append(_playbook_text(project))
    return "\n".join(part for part in parts if part)


def normalize_narrative_map(raw: Any, paras: dict[int, Paragraph]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """서사 지도를 정리한다. 원고에 없는 인용이 붙은 사건은 버리고 목록을 돌려준다."""
    data = raw if isinstance(raw, dict) else {}
    dropped: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for item in data.get("events") or []:
        if not isinstance(item, dict):
            continue
        para = _int(item.get("para"))
        quote = _text(item.get("quote"))
        if para not in paras:
            dropped.append({"where": "narrative_map.events", "reason": "para_invalid", "para": para})
            continue
        if quote and not quote_in_manuscript(paras, para, quote):
            dropped.append({"where": "narrative_map.events", "reason": "quote_invalid", "para": para})
            continue
        kind = str(item.get("kind") or "other")
        if kind not in {"revenge", "growth", "relation", "payoff", "emotion", "status", "other"}:
            kind = "other"
        events.append({"para": para, "kind": kind, "what": _text(item.get("what"), 80), "quote": quote})
    events.sort(key=lambda row: row["para"])

    threads = [s for s in (_text(v, 40) for v in (data.get("threads") or []) if isinstance(v, str)) if s][:MAX_LIST]
    payoffs = []
    for item in data.get("payoffs") or []:
        if isinstance(item, dict) and _text(item.get("what")):
            payoffs.append({"para": _int(item.get("para")), "what": _text(item.get("what"), 100)})
    settings = [s for s in (_text(v, 120) for v in (data.get("settings") or []) if isinstance(v, str)) if s][:MAX_LIST]
    return (
        {
            "events": events[:MAX_EVENTS],
            "threads": threads,
            "payoffs": payoffs[:MAX_LIST],
            "settings": settings,
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
    system_file="genre/web_fantasy_female_system.txt",
    map_prompt_file="genre/web_fantasy_female_map_prompt.txt",
    map_schema_file="genre/web_fantasy_female_map_schema.json",
    rubric_prompt_file="genre/web_fantasy_female_rubric_prompt.txt",
    rubric_schema_file="genre/web_fantasy_female_rubric_schema.json",
    map_label="서사 지도",
    map_key="narrative_map",
    usage_stage="genre_narrative_map",
    normalize_map=normalize_narrative_map,
    valid_keys=RUBRIC_KEYS,
    required_keys=lambda project, has_kept: required_keys(project, has_kept_avoid=has_kept),
    emphasis=_emphasis_text,
    broken_field=webnovel_genre_track.CLICHE_USED,
    kept_field=webnovel_genre_track.CLICHE_AVOID,
)


def run_web_fantasy_female_report(conn: sqlite3.Connection, project_id: int, paragraphs: list[Paragraph], **kwargs: Any) -> dict[str, Any]:
    return genre_common.run_report(SPEC, conn, project_id, paragraphs, **kwargs)


# ── 카드와 검증 ───────────────────────────────────────────────

# 서사·감정·반전에 닿는 문제라 모두 지적형(수정안 없음). 신분·세계 설정 모순의
# 수정안은 웹소설 러너의 정합성 카드가 설정집과 대조해서 만든다.
CARD_TAGS = {
    "narrative_balance": "narrative_imbalance",
    "payoff_timing": "payoff_off",
    "emotion_line": "emotion_told",
    "world_status": "world_mismatch",
    "status_order": "status_break",
    "world_split": "world_mix",
    "kept_avoid": "avoid_gap",
}
CARD_LABELS = {
    "narrative_imbalance": "서사 균형 어긋남",
    "payoff_off": "반전 타이밍 어긋남",
    "emotion_told": "감정 설명 과잉",
    "world_mismatch": "신분·세계 설정 모순",
    "status_break": "신분 질서 어긋남",
    "world_mix": "세계 규칙 섞임",
    "avoid_gap": "피하고 싶은 클리셰",
}
_CORE_KEYS = {"narrative_balance", "payoff_timing"}
PROTECTED_KINDS = {"revenge", "growth", "relation", "payoff", "emotion", "status"}

WEB_FANTASY_FEMALE_TOUCH_WARNING = {
    "code": "web_fantasy_female_touch",
    "severity": "info",
    "message": "복수·반전·감정선이 움직이는 장면이라 수정안 대신 지적만 드려요",
}
TOUCH_WARNING = WEB_FANTASY_FEMALE_TOUCH_WARNING


def protected_paragraphs(genre: dict[str, Any] | None) -> set[int]:
    """서사 지도에서 복수·성장·관계·반전·감정·신분이 움직이는 문단 번호."""
    out: set[int] = set()
    narrative_map = (genre or {}).get("narrative_map") if isinstance(genre, dict) else None
    if not isinstance(narrative_map, dict):
        return out
    for event in narrative_map.get("events") or []:
        if isinstance(event, dict) and event.get("kind") in PROTECTED_KINDS:
            out.add(_int(event.get("para")))
    for row in narrative_map.get("payoffs") or []:
        if isinstance(row, dict):
            out.add(_int(row.get("para")))
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


MD_TITLE = "웹소설 여성향판타지 진단"
MD_LABELS = {
    "narrative_balance": "서사 균형",
    "payoff_timing": "반전·사이다 타이밍",
    "emotion_line": "감정선",
    "world_status": "세계·신분 설정",
    "status_order": "신분 질서",
    "world_split": "세계 규칙 구분",
    "kept_avoid": "피하고 싶은 클리셰",
}
