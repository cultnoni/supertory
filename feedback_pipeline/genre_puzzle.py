"""수수께끼형(미스터리·추리, 스릴러·호러) 장르 레이어.

웹소설 러너(``runner.run_feedback``)의 정합성 단계 뒤, 일반 리포트 앞에서 돈다.
두 번 부른다. 1) 정보 공개 지도 2) 장르 진단 루브릭(총평·강점·우선 과제 포함).
결과는 ``report["genre"]``에 붙고, 보완·문제 판정은 지적형 카드가 된다(수정안 없음).
두 묶음이 함께 쓰는 도구는 ``genre_common``에 있다.

설계는 docs/genre-pipeline.md의 「수수께끼형」을 따른다.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import genre_track
from feedback_pipeline import genre_common
from feedback_pipeline.genre_common import (
    INTENT_OK_WARNING,
    LayerSpec,
    VERDICTS,
    broken_promise_text,
    missing_keys,
    promise_covers,
    quote_in_manuscript,
)
from feedback_pipeline.paragraphs import Paragraph

GROUP = genre_track.GROUP_PUZZLE

# 항목 키 → 화면에 보이는 이름은 web/feedback_panel.js에도 있다(로케일 키 app.장르_진단_항목_*).
RUBRIC_KEYS = (
    "clue_fairness",
    "disclosure_order",
    "payoff",
    "twist_plausibility",
    "tension_curve",
    "threat_motive",
    "deception",
    "kept_promise",
)

# 하위 장르별로 더 무겁게 보는 것 (설계 문서 「장르별 추가」)
_EMPHASIS = {
    "honkaku": "본격 추리로 읽는다. 단서의 공정성과 풀이 논리를 더 무겁게 본다.",
    "psycho": "심리 스릴러로 읽는다. 정보 공개와 서술자 신뢰를 더 무겁게 본다.",
}
_EMPHASIS_BY_MAIN = {
    "mystery": "미스터리·추리로 읽는다. threat_motive는 넣지 않는다.",
    "thriller": "스릴러로 읽는다. threat_motive를 반드시 넣는다.",
}

MAX_EVENTS = 10
MAX_LIST = 8

_text = genre_common.text
_int = genre_common.to_int


def required_keys(main_genre: object, *, has_kept_promise: bool) -> list[str]:
    """이 작품에서 반드시 판정해야 하는 항목."""
    keys = ["clue_fairness", "disclosure_order", "payoff", "twist_plausibility", "tension_curve", "deception"]
    if str(main_genre or "").strip() == "thriller":
        keys.insert(5, "threat_motive")
    if has_kept_promise:
        keys.append("kept_promise")
    return keys


def _emphasis_text(project: dict[str, Any]) -> str:
    main = str(project.get("main_genre") or "").strip()
    if main in {"mystery_detective", "detective"}:
        main = "mystery"
    parts = [_EMPHASIS_BY_MAIN.get(main, "")]
    sub = str(project.get("sub_genre") or "").strip()
    if sub in _EMPHASIS:
        parts.append(_EMPHASIS[sub])
    return " ".join(part for part in parts if part)


def normalize_clue_map(raw: Any, paras: dict[int, Paragraph]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """정보 공개 지도를 정리한다. 원고에 없는 인용이 붙은 사건은 버리고 목록을 돌려준다."""
    data = raw if isinstance(raw, dict) else {}
    dropped: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for item in data.get("events") or []:
        if not isinstance(item, dict):
            continue
        para = _int(item.get("para"))
        quote = _text(item.get("quote"))
        if para not in paras:
            dropped.append({"where": "clue_map.events", "reason": "para_invalid", "para": para})
            continue
        if quote and not quote_in_manuscript(paras, para, quote):
            dropped.append({"where": "clue_map.events", "reason": "quote_invalid", "para": para})
            continue
        kind = str(item.get("kind") or "other")
        if kind not in {"clue", "red_herring", "reveal", "setup", "tension", "other"}:
            kind = "other"
        events.append({"para": para, "kind": kind, "what": _text(item.get("what"), 80), "quote": quote})
    events.sort(key=lambda row: row["para"])

    def strings(key: str) -> list[str]:
        return [s for s in (_text(v, 100) for v in (data.get(key) or []) if isinstance(v, str)) if s][:MAX_LIST]

    def questions(key: str) -> list[dict[str, Any]]:
        rows = []
        for item in data.get(key) or []:
            if isinstance(item, dict) and _text(item.get("question")):
                rows.append({"question": _text(item.get("question"), 100), "para": _int(item.get("para"))})
        return rows[:MAX_LIST]

    turns = []
    for item in data.get("turns") or []:
        if isinstance(item, dict) and _text(item.get("what")):
            turns.append({"para": _int(item.get("para")), "what": _text(item.get("what"), 100)})
    return (
        {
            "events": events[:MAX_EVENTS],
            "reader_knows_at_end": strings("reader_knows_at_end"),
            "reader_does_not_know": strings("reader_does_not_know"),
            "open_questions": questions("open_questions"),
            "resolved_questions": questions("resolved_questions"),
            "turns": turns[:MAX_LIST],
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
    system_file="genre/puzzle_system.txt",
    map_prompt_file="genre/puzzle_clue_map_prompt.txt",
    map_schema_file="genre/puzzle_clue_map_schema.json",
    rubric_prompt_file="genre/puzzle_rubric_prompt.txt",
    rubric_schema_file="genre/puzzle_rubric_schema.json",
    map_label="정보 공개 지도",
    map_key="clue_map",
    usage_stage="genre_clue_map",
    normalize_map=normalize_clue_map,
    valid_keys=RUBRIC_KEYS,
    required_keys=lambda project, has_kept: required_keys(project.get("main_genre"), has_kept_promise=has_kept),
    emphasis=_emphasis_text,
)


def run_puzzle_report(conn: sqlite3.Connection, project_id: int, paragraphs: list[Paragraph], **kwargs: Any) -> dict[str, Any]:
    return genre_common.run_report(SPEC, conn, project_id, paragraphs, **kwargs)


# ── 카드와 검증 ───────────────────────────────────────────────

# 루브릭 항목 → 카드 유형 태그. 단서·반전에 닿는 문제라 모두 지적형(수정안 없음)이다.
CARD_TAGS = {
    "clue_fairness": "clue_gap",  # 단서 누락·모호
    "disclosure_order": "reveal_timing",  # 정보 노출 시점
    "payoff": "dangling_thread",  # 방치된 복선·의문
    "twist_plausibility": "twist_logic",  # 반전의 개연성
    "tension_curve": "tension_slack",  # 늘어지는 긴장
    "threat_motive": "motive_gap",  # 위협·동기 불일치
    "deception": "narration_trick",  # 서술 속임수 위반
    "kept_promise": "promise_gap",  # 지키고 싶은 약속이 흔들림
}
CARD_LABELS = {
    "clue_gap": "단서 누락·모호",
    "reveal_timing": "정보 노출 시점",
    "dangling_thread": "방치된 복선",
    "twist_logic": "반전의 개연성",
    "tension_slack": "늘어지는 긴장",
    "motive_gap": "위협·동기 불일치",
    "narration_trick": "서술 속임수",
    "promise_gap": "지키고 싶은 약속",
}
# 이 항목들은 풀이·반전에 직접 닿으므로 중요도를 한 단계 올린다(설계 문서 「중요도」).
_CORE_KEYS = {"clue_fairness", "twist_plausibility"}
CLUE_KINDS = {"clue", "red_herring", "reveal", "setup"}

CLUE_TOUCH_WARNING = {
    "code": "clue_touch",
    "severity": "info",
    "message": "단서나 반전에 닿는 부분이라 수정안 대신 지적만 드려요",
}
TOUCH_WARNING = CLUE_TOUCH_WARNING


def clue_paragraphs(genre: dict[str, Any] | None) -> set[int]:
    """정보 공개 지도에서 단서·반전·의문이 걸린 문단 번호."""
    out: set[int] = set()
    clue_map = (genre or {}).get("clue_map") if isinstance(genre, dict) else None
    if not isinstance(clue_map, dict):
        return out
    for event in clue_map.get("events") or []:
        if isinstance(event, dict) and event.get("kind") in CLUE_KINDS:
            out.add(_int(event.get("para")))
    for key in ("turns", "open_questions", "resolved_questions"):
        for row in clue_map.get(key) or []:
            if isinstance(row, dict):
                out.add(_int(row.get("para")))
    out.discard(0)
    return out


protected_paragraphs = clue_paragraphs


def strip_suggestion_near_clues(card: dict[str, Any], clue_paras: set[int]) -> bool:
    """수정안 카드가 단서·반전 문단에 닿으면 수정안을 지우고 지적만 남긴다. 바꿨으면 True."""
    return genre_common.strip_suggestion_near(card, clue_paras, CLUE_TOUCH_WARNING)


def card_impact(key: str, verdict: str) -> int:
    return genre_common.card_impact(key, verdict, _CORE_KEYS)


def build_genre_cards(
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


def build_cards(genre, paragraphs, scene_id, *, promise_broken=""):
    return build_genre_cards(genre, paragraphs, scene_id, promise_broken=promise_broken)


# 리포트 마크다운(``## 장르 진단 (수수께끼형)``)에 쓰는 이름
MD_TITLE = "장르 진단 (수수께끼형)"
MD_LABELS = {
    "clue_fairness": "단서의 공정성",
    "disclosure_order": "정보 공개 순서",
    "payoff": "복선·의문 회수",
    "twist_plausibility": "반전의 개연성",
    "tension_curve": "긴장 곡선",
    "threat_motive": "위협·동기 일관성",
    "deception": "속임수",
    "kept_promise": "지키고 싶은 약속",
}

__all__ = [
    "GROUP", "RUBRIC_KEYS", "VERDICTS", "INTENT_OK_WARNING", "SPEC", "CARD_TAGS", "CARD_LABELS",
    "required_keys", "normalize_clue_map", "normalize_rubric", "missing_keys", "quote_in_manuscript",
    "promise_covers", "broken_promise_text", "run_puzzle_report", "clue_paragraphs",
    "strip_suggestion_near_clues", "card_impact", "build_genre_cards",
]
