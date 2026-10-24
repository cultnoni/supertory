"""세계형(SF, 정통판타지) 장르 레이어.

수수께끼형과 같은 방식으로 웹소설 러너 안에서 두 번 부른다.
1) 설정 지도(규칙·용어·설정이 어디서 드러나는지)  2) 장르 진단 루브릭.
정통판타지는 웹소설 판타지와 따로 장르로 둔 이유가 있어야 하므로, SF와 공통인
세계 규칙 항목에 마법·힘의 규칙, 설정의 깊이, 서사 규모를 더한다.
두 묶음이 함께 쓰는 도구는 ``genre_common``에 있다.

설계는 docs/genre-pipeline.md의 「세계형」을 따른다.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import genre_track
from feedback_pipeline import genre_common
from feedback_pipeline.genre_common import LayerSpec, quote_in_manuscript
from feedback_pipeline.paragraphs import Paragraph

GROUP = genre_track.GROUP_WORLD

RUBRIC_KEYS = (
    "rule_consistency",
    "exposition",
    "plausibility",
    "ripple",
    "term_consistency",
    "power_rules",
    "setting_depth",
    "scale",
    "kept_promise",
)

_EMPHASIS_BY_MAIN = {
    "sf": "SF로 읽는다. 기술과 사회 규칙의 파급(ripple)을 더 무겁게 본다. power_rules, setting_depth, scale은 넣지 않는다.",
    "traditional": (
        "정통판타지로 읽는다. 마법·힘의 규칙(power_rules), 설정의 깊이(setting_depth), 서사 규모(scale)를 "
        "반드시 넣고, 설정을 푸는 방식(exposition)을 더 무겁게 본다. ripple은 넣지 않는다."
    ),
}

MAX_EVENTS = 10
MAX_LIST = 8

_text = genre_common.text
_int = genre_common.to_int


def required_keys(main_genre: object, *, has_kept_promise: bool) -> list[str]:
    """이 작품에서 반드시 판정해야 하는 항목."""
    main = str(main_genre or "").strip()
    if main == "traditional":
        keys = ["rule_consistency", "exposition", "plausibility", "term_consistency",
                "power_rules", "setting_depth", "scale"]
    else:
        keys = ["rule_consistency", "exposition", "plausibility", "ripple", "term_consistency"]
    if has_kept_promise:
        keys.append("kept_promise")
    return keys


def _emphasis_text(project: dict[str, Any]) -> str:
    return _EMPHASIS_BY_MAIN.get(str(project.get("main_genre") or "").strip(), "")


def normalize_world_map(raw: Any, paras: dict[int, Paragraph]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """설정 지도를 정리한다. 원고에 없는 인용이 붙은 사건은 버리고 목록을 돌려준다."""
    data = raw if isinstance(raw, dict) else {}
    dropped: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for item in data.get("events") or []:
        if not isinstance(item, dict):
            continue
        para = _int(item.get("para"))
        quote = _text(item.get("quote"))
        if para not in paras:
            dropped.append({"where": "world_map.events", "reason": "para_invalid", "para": para})
            continue
        if quote and not quote_in_manuscript(paras, para, quote):
            dropped.append({"where": "world_map.events", "reason": "quote_invalid", "para": para})
            continue
        kind = str(item.get("kind") or "other")
        if kind not in {"rule", "term", "setting", "consequence", "exposition", "change", "other"}:
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
    system_file="genre/world_system.txt",
    map_prompt_file="genre/world_map_prompt.txt",
    map_schema_file="genre/world_map_schema.json",
    rubric_prompt_file="genre/world_rubric_prompt.txt",
    rubric_schema_file="genre/world_rubric_schema.json",
    map_label="설정 지도",
    map_key="world_map",
    usage_stage="genre_world_map",
    normalize_map=normalize_world_map,
    valid_keys=RUBRIC_KEYS,
    required_keys=lambda project, has_kept: required_keys(project.get("main_genre"), has_kept_promise=has_kept),
    emphasis=_emphasis_text,
)


def run_world_report(conn: sqlite3.Connection, project_id: int, paragraphs: list[Paragraph], **kwargs: Any) -> dict[str, Any]:
    return genre_common.run_report(SPEC, conn, project_id, paragraphs, **kwargs)


# ── 카드와 검증 ───────────────────────────────────────────────

# 규칙·설정에 닿는 문제라 모두 지적형(수정안 없음)이다. 설정 모순·용어 불일치 수정안은
# 웹소설 러너의 정합성 카드가 설정집과 대조해서 만든다.
CARD_TAGS = {
    "rule_consistency": "rule_break",
    "exposition": "info_dump",
    "plausibility": "plausibility_gap",
    "ripple": "ripple_gap",
    "term_consistency": "term_mismatch",
    "power_rules": "power_rule_break",
    "setting_depth": "setting_ornament",
    "scale": "scale_stall",
    "kept_promise": "promise_gap",
}
CARD_LABELS = {
    "rule_break": "규칙 위반",
    "info_dump": "설정 덤프",
    "plausibility_gap": "개연성의 틈",
    "ripple_gap": "파급 누락",
    "term_mismatch": "용어 불일치",
    "power_rule_break": "힘의 규칙 위반",
    "setting_ornament": "장식뿐인 설정",
    "scale_stall": "서사 규모 정체",
    "promise_gap": "지키고 싶은 약속",
}
_CORE_KEYS = {"rule_consistency", "power_rules"}
PROTECTED_KINDS = {"rule", "term", "setting", "change"}

RULE_TOUCH_WARNING = {
    "code": "rule_touch",
    "severity": "info",
    "message": "세계의 규칙·설정을 세우는 부분이라 수정안 대신 지적만 드려요",
}
TOUCH_WARNING = RULE_TOUCH_WARNING


def protected_paragraphs(genre: dict[str, Any] | None) -> set[int]:
    """설정 지도에서 규칙·용어·설정이 세워지거나 바뀐 문단 번호."""
    out: set[int] = set()
    world_map = (genre or {}).get("world_map") if isinstance(genre, dict) else None
    if not isinstance(world_map, dict):
        return out
    for event in world_map.get("events") or []:
        if isinstance(event, dict) and event.get("kind") in PROTECTED_KINDS:
            out.add(_int(event.get("para")))
    for key in ("terms", "changes"):
        for row in world_map.get(key) or []:
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


MD_TITLE = "장르 진단 (세계형)"
MD_LABELS = {
    "rule_consistency": "세계 규칙의 일관성",
    "exposition": "설정 풀어 주기",
    "plausibility": "개연성",
    "ripple": "파급",
    "term_consistency": "용어 일관성",
    "power_rules": "마법·힘의 규칙",
    "setting_depth": "설정의 깊이",
    "scale": "서사 규모",
    "kept_promise": "지키고 싶은 약속",
}
