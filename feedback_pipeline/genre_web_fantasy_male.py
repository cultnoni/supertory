"""무협판타지형(웹소설) 장르 레이어.

로맨스형과 같은 방식으로 웹소설 러너 안에서 두 번 부른다.
1) 성장·설정 지도(성장·전투·설정이 어디서 어떻게 움직이는지)  2) 장르 진단 루브릭.
판타지(현판·어반판타지 포함)·무협·사극·대체역사·스포츠 하위 장르를 하나로 묶고,
기존 기준집(data/genre_playbooks.json의 fantasy_male + delta)을 쓴다.

실제 저장값은 대부분 main_genre="fantasy", sub_genre="male", genre_detail=무협/정통무협/
대체역사/현판/어반판타지/정통판타지/이세계/스포츠 중 하나다(``genre_clusters.GENRE_DETAIL_OPTIONS``).
main_genre가 "martial"/"historical"/"sports"로 저장된 예전 데이터도 지원한다
(``webnovel_genre_track.GROUP_BY_MAIN``, ``genre_clusters.playbook_lookup_keys``가 호환을 맡는다).

두 묶음이 함께 쓰는 도구는 ``genre_common``에 있다.
설계는 docs/webnovel-genre-pipeline.md의 「무협판타지형」을 따른다.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import webnovel_genre_track
from feedback_pipeline import genre_common
from feedback_pipeline.genre_common import LayerSpec, quote_in_manuscript
from feedback_pipeline.paragraphs import Paragraph

GROUP = webnovel_genre_track.GROUP_FANTASY_MALE

RUBRIC_KEYS = (
    "growth_pace",
    "exposition",
    "impact",
    "trope_use",
    "term_consistency",
    "faction_consistency",
    "rule_consistency",
    "history_crossover",
    "real_world_fit",
    "match_rhythm",
    "kept_avoid",
)

_BASE_KEYS = ["growth_pace", "exposition", "impact", "trope_use", "term_consistency"]
_EXTRA_BY_DETAIL = {
    "murim": "faction_consistency",
    "murim_classic": "faction_consistency",
    "traditional": "rule_consistency",
    "alt_history": "history_crossover",
    "urban": "real_world_fit",
    "hidden_world": "real_world_fit",
    "sports": "match_rhythm",
}
_EMPHASIS_BY_DETAIL = {
    "murim": "무협으로 읽는다. 문파·배분(세대) 설정의 일관성(faction_consistency)을 더 무겁게 본다.",
    "murim_classic": "정통무협으로 읽는다. 문파·배분(세대) 설정의 일관성(faction_consistency)을 더 무겁게 본다.",
    "traditional": "정통판타지로 읽는다. 세계 규칙의 일관성(rule_consistency)을 더 무겁게 본다.",
    "alt_history": "대체역사로 읽는다. 역사적 사실과의 교차점(history_crossover)을 더 무겁게 본다.",
    "urban": "현대판타지로 읽는다. 현실 설정과 능력의 결합(real_world_fit)을 더 무겁게 본다.",
    "hidden_world": "어반판타지로 읽는다. 현실 설정과 능력의 결합(real_world_fit)을 더 무겁게 본다.",
    "sports": "스포츠물로 읽는다. 경기·훈련 장면의 리듬(match_rhythm)을 더 무겁게 본다.",
}

MAX_EVENTS = 10
MAX_LIST = 8

_text = genre_common.text
_int = genre_common.to_int

# main_genre="historical"/"sports"는 playbook_lookup_keys가 detail로 바꿔 주지만,
# 루브릭 필요 항목·강조문은 여기서도 같은 detail을 알아야 한다.
_LEGACY_MAIN_TO_DETAIL = {"historical": "alt_history", "sports": "sports"}


def _effective_detail(project: dict[str, Any]) -> str:
    main = str(project.get("main_genre") or "").strip()
    if main in _LEGACY_MAIN_TO_DETAIL:
        return _LEGACY_MAIN_TO_DETAIL[main]
    return str(project.get("genre_detail") or "").strip()


def required_keys(project: dict[str, Any], *, has_kept_avoid: bool) -> list[str]:
    keys = list(_BASE_KEYS)
    extra = _EXTRA_BY_DETAIL.get(_effective_detail(project))
    if extra:
        keys.append(extra)
    if has_kept_avoid:
        keys.append("kept_avoid")
    return keys


def _playbook_text(project: dict[str, Any]) -> str:
    """기존 판타지 기준집에서 판정에 필요한 부분만 뽑는다. 도우미 A그룹과 같은 범위."""
    row = dict(project)
    if not str(row.get("genre_detail") or "").strip():
        row["genre_detail"] = _effective_detail(row)
    return genre_common.playbook_emphasis_text(row, heading="[판타지 기준집]")


def _emphasis_text(project: dict[str, Any]) -> str:
    parts = [_EMPHASIS_BY_DETAIL.get(_effective_detail(project), "")]
    parts.append(_playbook_text(project))
    return "\n".join(part for part in parts if part)


def normalize_growth_map(raw: Any, paras: dict[int, Paragraph]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """성장·설정 지도를 정리한다. 원고에 없는 인용이 붙은 사건은 버리고 목록을 돌려준다."""
    data = raw if isinstance(raw, dict) else {}
    dropped: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for item in data.get("events") or []:
        if not isinstance(item, dict):
            continue
        para = _int(item.get("para"))
        quote = _text(item.get("quote"))
        if para not in paras:
            dropped.append({"where": "growth_map.events", "reason": "para_invalid", "para": para})
            continue
        if quote and not quote_in_manuscript(paras, para, quote):
            dropped.append({"where": "growth_map.events", "reason": "quote_invalid", "para": para})
            continue
        kind = str(item.get("kind") or "other")
        if kind not in {"rule", "term", "setting", "growth", "impact", "change", "other"}:
            kind = "other"
        events.append({"para": para, "kind": kind, "what": _text(item.get("what"), 80), "quote": quote})
    events.sort(key=lambda row: row["para"])

    rules = [s for s in (_text(v, 120) for v in (data.get("rules") or []) if isinstance(v, str)) if s][:MAX_LIST]
    terms = []
    for item in data.get("terms") or []:
        if isinstance(item, dict) and _text(item.get("term")):
            terms.append(
                {"term": _text(item.get("term"), 40), "meaning": _text(item.get("meaning"), 100), "para": _int(item.get("para"))}
            )
    changes = []
    for item in data.get("changes") or []:
        if isinstance(item, dict) and _text(item.get("what")):
            changes.append({"para": _int(item.get("para")), "what": _text(item.get("what"), 100)})
    return (
        {"events": events[:MAX_EVENTS], "rules": rules, "terms": terms[:MAX_LIST], "changes": changes[:MAX_LIST]},
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
    system_file="genre/web_fantasy_male_system.txt",
    map_prompt_file="genre/web_fantasy_male_map_prompt.txt",
    map_schema_file="genre/web_fantasy_male_map_schema.json",
    rubric_prompt_file="genre/web_fantasy_male_rubric_prompt.txt",
    rubric_schema_file="genre/web_fantasy_male_rubric_schema.json",
    map_label="성장·설정 지도",
    map_key="growth_map",
    usage_stage="genre_growth_map",
    normalize_map=normalize_growth_map,
    valid_keys=RUBRIC_KEYS,
    required_keys=lambda project, has_kept: required_keys(project, has_kept_avoid=has_kept),
    emphasis=_emphasis_text,
    broken_field=webnovel_genre_track.CLICHE_USED,
    kept_field=webnovel_genre_track.CLICHE_AVOID,
)


def run_web_fantasy_male_report(conn: sqlite3.Connection, project_id: int, paragraphs: list[Paragraph], **kwargs: Any) -> dict[str, Any]:
    return genre_common.run_report(SPEC, conn, project_id, paragraphs, **kwargs)


# ── 카드와 검증 ───────────────────────────────────────────────

# 성장·전투·설정에 닿는 문제라 모두 지적형(수정안 없음). 능력치·설정 모순, 용어 불일치
# 수정안은 웹소설 러너의 정합성 카드가 설정집과 대조해서 만든다.
CARD_TAGS = {
    "growth_pace": "pace_off",
    "exposition": "info_dump",
    "impact": "impact_weak",
    "trope_use": "trope_fatigue",
    "term_consistency": "term_mismatch",
    "faction_consistency": "faction_break",
    "rule_consistency": "rule_break",
    "history_crossover": "history_gap",
    "real_world_fit": "real_world_gap",
    "match_rhythm": "match_pacing_off",
    "kept_avoid": "avoid_gap",
}
CARD_LABELS = {
    "pace_off": "성장 템포 어긋남",
    "info_dump": "설정 덤프",
    "impact_weak": "타격감 부족",
    "trope_fatigue": "장르 공식 피로",
    "term_mismatch": "용어 불일치",
    "faction_break": "문파·배분 불일치",
    "rule_break": "세계 규칙 위반",
    "history_gap": "역사 교차점 어긋남",
    "real_world_gap": "현실 설정과 능력 결합 약함",
    "match_pacing_off": "경기·훈련 리듬 어긋남",
    "avoid_gap": "피하고 싶은 클리셰",
}
_CORE_KEYS = {"growth_pace", "impact"}
PROTECTED_KINDS = {"rule", "term", "setting", "growth", "impact", "change"}

WEB_FANTASY_MALE_TOUCH_WARNING = {
    "code": "web_fantasy_male_touch",
    "severity": "info",
    "message": "성장·전투·설정이 움직이는 장면이라 수정안 대신 지적만 드려요",
}
TOUCH_WARNING = WEB_FANTASY_MALE_TOUCH_WARNING


def protected_paragraphs(genre: dict[str, Any] | None) -> set[int]:
    """성장·설정 지도에서 규칙·용어·설정·성장·전투가 움직이는 문단 번호."""
    out: set[int] = set()
    growth_map = (genre or {}).get("growth_map") if isinstance(genre, dict) else None
    if not isinstance(growth_map, dict):
        return out
    for event in growth_map.get("events") or []:
        if isinstance(event, dict) and event.get("kind") in PROTECTED_KINDS:
            out.add(_int(event.get("para")))
    for key in ("terms", "changes"):
        for row in growth_map.get(key) or []:
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


MD_TITLE = "웹소설 무협판타지 진단"
MD_LABELS = {
    "growth_pace": "성장 템포",
    "exposition": "설정 전달",
    "impact": "타격감",
    "trope_use": "장르 공식 활용",
    "term_consistency": "용어 일관성",
    "faction_consistency": "문파·배분 일관성",
    "rule_consistency": "세계 규칙의 일관성",
    "history_crossover": "역사적 사실과의 교차점",
    "real_world_fit": "현실 설정과 능력의 결합",
    "match_rhythm": "경기·훈련 장면의 리듬",
    "kept_avoid": "피하고 싶은 클리셰",
}
