"""로맨스형(웹소설) 장르 레이어.

장르문학 수수께끼형·세계형·관계형과 같은 방식으로 웹소설 러너 안에서 두 번 부른다.
1) 케미 지도(케미·밀당이 어디서 어떻게 움직이는지)  2) 장르 진단 루브릭.
판단 기준은 기존 로맨스 기준집(data/genre_playbooks.json의 romance_modern / romance_romfant /
romance_bl / romance_gl + delta)을 쓴다. 작품의 main_genre/sub_genre/genre_detail로
``genre_clusters.playbook_lookup_keys()``가 이미 올바른 기준집을 골라 주므로 여기서 다시
분기하지 않는다.

장르문학 관계형(``feedback_pipeline/genre_relation.py``)과 판단 기준이 완전히 다르다.
장르문학 로맨스는 "관계가 문학적으로 작동하는가"를 보고, 여기는 "다음 화가 궁금한가"를 본다.
그래서 그룹 id·카드 유형·MD_TITLE을 모두 분리했다(결정 사항 2번, docs/webnovel-genre-pipeline.md).

설계는 docs/webnovel-genre-pipeline.md의 「로맨스형」을 따른다.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import webnovel_genre_track
from feedback_pipeline import genre_common
from feedback_pipeline.genre_common import LayerSpec, quote_in_manuscript
from feedback_pipeline.paragraphs import Paragraph

GROUP = webnovel_genre_track.GROUP_ROMANCE

RUBRIC_KEYS = (
    "chemistry",
    "banter_pacing",
    "conflict_credibility",
    "sub_balance",
    "cliche_use",
    "kept_avoid",
)

MAX_EVENTS = 10
MAX_LIST = 8

_text = genre_common.text
_int = genre_common.to_int


def required_keys(has_kept_avoid: bool) -> list[str]:
    keys = ["chemistry", "banter_pacing", "conflict_credibility", "sub_balance", "cliche_use"]
    if has_kept_avoid:
        keys.append("kept_avoid")
    return keys


def _playbook_text(project: dict[str, Any]) -> str:
    """기존 로맨스 기준집에서 판정에 필요한 부분만 뽑는다. 도우미 A그룹과 같은 범위."""
    return genre_common.playbook_emphasis_text(project, heading="[로맨스 기준집]")


def _emphasis_text(project: dict[str, Any]) -> str:
    return _playbook_text(project)


def normalize_chemistry_map(raw: Any, paras: dict[int, Paragraph]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """케미 지도를 정리한다. 원고에 없는 인용이 붙은 사건은 버리고 목록을 돌려준다."""
    data = raw if isinstance(raw, dict) else {}
    dropped: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for item in data.get("events") or []:
        if not isinstance(item, dict):
            continue
        para = _int(item.get("para"))
        quote = _text(item.get("quote"))
        if para not in paras:
            dropped.append({"where": "chemistry_map.events", "reason": "para_invalid", "para": para})
            continue
        if quote and not quote_in_manuscript(paras, para, quote):
            dropped.append({"where": "chemistry_map.events", "reason": "quote_invalid", "para": para})
            continue
        kind = str(item.get("kind") or "other")
        if kind not in {"closer", "distance", "banter", "conflict", "skinship", "cliche_beat", "other"}:
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
    system_file="genre/web_romance_system.txt",
    map_prompt_file="genre/web_romance_map_prompt.txt",
    map_schema_file="genre/web_romance_map_schema.json",
    rubric_prompt_file="genre/web_romance_rubric_prompt.txt",
    rubric_schema_file="genre/web_romance_rubric_schema.json",
    map_label="케미 지도",
    map_key="chemistry_map",
    usage_stage="genre_chemistry_map",
    normalize_map=normalize_chemistry_map,
    valid_keys=RUBRIC_KEYS,
    required_keys=lambda project, has_kept: required_keys(has_kept),
    emphasis=_emphasis_text,
    broken_field=webnovel_genre_track.CLICHE_USED,
    kept_field=webnovel_genre_track.CLICHE_AVOID,
)


def run_web_romance_report(conn: sqlite3.Connection, project_id: int, paragraphs: list[Paragraph], **kwargs: Any) -> dict[str, Any]:
    return genre_common.run_report(SPEC, conn, project_id, paragraphs, **kwargs)


# ── 카드와 검증 ───────────────────────────────────────────────

# 케미·밀당·갈등에 닿는 문제라 모두 지적형(수정안 없음)이다. 호칭·말투 불일치 수정안은
# 웹소설 러너의 정합성 카드가 설정집과 대조해서 만든다.
CARD_TAGS = {
    "chemistry": "chemistry_weak",
    "banter_pacing": "banter_off",
    "conflict_credibility": "conflict_forced",
    "sub_balance": "sub_overload",
    "cliche_use": "cliche_fatigue",
    "kept_avoid": "avoid_gap",
}
CARD_LABELS = {
    "chemistry_weak": "케미 약함",
    "banter_off": "밀당 리듬 어긋남",
    "conflict_forced": "갈등 작위적",
    "sub_overload": "서브캐릭터 과잉",
    "cliche_fatigue": "클리셰 피로",
    "avoid_gap": "피하고 싶은 클리셰",
}
_CORE_KEYS = {"chemistry", "banter_pacing"}
PROTECTED_KINDS = {"closer", "distance", "banter", "conflict"}

WEB_ROMANCE_TOUCH_WARNING = {
    "code": "web_romance_touch",
    "severity": "info",
    "message": "케미·밀당이 움직이는 장면이라 수정안 대신 지적만 드려요",
}
TOUCH_WARNING = WEB_ROMANCE_TOUCH_WARNING


def protected_paragraphs(genre: dict[str, Any] | None) -> set[int]:
    """케미 지도에서 관계가 가까워지거나 멀어지거나 밀당·갈등이 생기는 문단 번호."""
    out: set[int] = set()
    chemistry_map = (genre or {}).get("chemistry_map") if isinstance(genre, dict) else None
    if not isinstance(chemistry_map, dict):
        return out
    for event in chemistry_map.get("events") or []:
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


MD_TITLE = "웹소설 로맨스 진단"
MD_LABELS = {
    "chemistry": "케미",
    "banter_pacing": "밀당 호흡",
    "conflict_credibility": "갈등의 설득력",
    "sub_balance": "서브 비중",
    "cliche_use": "클리셰 활용",
    "kept_avoid": "피하고 싶은 클리셰",
}
