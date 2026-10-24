"""수수께끼형(미스터리·추리, 스릴러·호러) 장르 리포트.

웹소설 러너(``runner.run_feedback``)의 정합성 단계 뒤, 일반 리포트 앞에서 돈다.
두 번 부른다. 1) 정보 공개 지도 2) 장르 진단 루브릭(총평·강점·우선 과제 포함).
결과는 ``report["genre"]``에 붙고, 문장 수정안이나 카드는 만들지 않는다(카드는 4단계).

설계는 docs/genre-pipeline.md의 「수수께끼형」을 따른다.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import genre_track
from feedback_pipeline import prompt_loader
from feedback_pipeline.literature_cache import build_cached_system
from feedback_pipeline.paragraphs import Paragraph, find_in_paragraph

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
VERDICTS = ("works", "room", "problem", "none")

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


def _text(value: object, limit: int = 0) -> str:
    out = " ".join(str(value or "").split())
    return out[:limit] if limit else out


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _para_map(paragraphs: list[Paragraph]) -> dict[int, Paragraph]:
    return {int(p.number): p for p in paragraphs}


def quote_in_manuscript(paras: dict[int, Paragraph], para: int, quote: str) -> bool:
    """원고 문단 안에 인용이 실제로 있는가. 문단 번호가 틀려도 근처 문단에서 찾으면 인정하지 않는다."""
    target = paras.get(int(para))
    if target is None or not str(quote or "").strip():
        return False
    return find_in_paragraph(target, quote) is not None


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


def _clean_evidence(rows: Any, paras: dict[int, Paragraph]) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    removed = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        para = _int(row.get("para"))
        quote = _text(row.get("quote"))
        if quote_in_manuscript(paras, para, quote):
            kept.append({"para": para, "quote": quote})
        else:
            removed += 1
    return kept, removed


def normalize_rubric(
    raw: Any,
    paras: dict[int, Paragraph],
    *,
    needed: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """루브릭 판정을 정리한다.

    - 원고에 없는 인용은 지운다. 근거가 하나도 안 남은 보완·문제 판정은 ``unverified``로 표시한다.
    - 의도적(intentional)인 problem은 room으로 낮춘다. 의도를 존중하되 전달이 약하면 알려 준다.
    - 필요한데 빠진 항목은 none으로 채운다(``missing``).
    """
    data = raw if isinstance(raw, dict) else {}
    dropped: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key not in RUBRIC_KEYS or key in by_key:
            continue
        verdict = str(item.get("verdict") or "none")
        if verdict not in VERDICTS:
            verdict = "none"
        intentional = bool(item.get("intentional"))
        evidence, removed = _clean_evidence(item.get("evidence"), paras)
        if removed:
            dropped.append({"where": f"rubric.{key}", "reason": "quote_invalid", "count": removed})
        if intentional and verdict == "problem":
            verdict = "room"
        by_key[key] = {
            "key": key,
            "verdict": verdict,
            "intentional": intentional,
            "note": _text(item.get("note"), 200),
            "evidence": evidence,
            "unverified": verdict in {"room", "problem"} and not evidence,
        }
    items: list[dict[str, Any]] = []
    for key in RUBRIC_KEYS:
        if key in by_key:
            items.append(by_key[key])
        elif key in needed:
            items.append(
                {"key": key, "verdict": "none", "intentional": False, "note": "", "evidence": [],
                 "unverified": False, "missing": True}
            )
    # 필요하지 않은 항목은 모델이 넣어도 버린다(예: 미스터리의 threat_motive)
    items = [item for item in items if item["key"] in needed]

    strengths: list[dict[str, Any]] = []
    for item in data.get("strengths") or []:
        if not isinstance(item, dict):
            continue
        para = _int(item.get("para"))
        quote = _text(item.get("quote"))
        if not _text(item.get("title")) or not quote_in_manuscript(paras, para, quote):
            dropped.append({"where": "rubric.strengths", "reason": "quote_invalid", "para": para})
            continue
        strengths.append(
            {"title": _text(item.get("title"), 60), "body": _text(item.get("body"), 200), "para": para, "quote": quote}
        )
    tasks = [
        {"title": _text(item.get("title"), 80), "why": _text(item.get("why"), 200)}
        for item in (data.get("top_tasks") or [])
        if isinstance(item, dict) and _text(item.get("title"))
    ]
    return (
        {
            "summary": _text(data.get("summary"), 500),
            "items": items,
            "strengths": strengths[:3],
            "top_tasks": tasks[:3],
        },
        dropped,
    )


def missing_keys(rubric: dict[str, Any]) -> list[str]:
    return [item["key"] for item in rubric.get("items") or [] if item.get("missing")]


def _project_row(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT cluster_id, main_genre, sub_genre, tory_priority_md, promise_broken, promise_kept "
        "FROM project WHERE id = ?",
        (int(project_id),),
    ).fetchone()
    return dict(row) if row is not None else {}


def run_puzzle_report(
    conn: sqlite3.Connection,
    project_id: int,
    paragraphs: list[Paragraph],
    *,
    shared_prefix: str,
    caller: Any,
    model: str,
    params: dict[str, Any],
    max_cost: float,
    add_usage: Any,
    check_cost: Any,
    invoke_parsed: Any,
) -> dict[str, Any]:
    """정보 공개 지도와 장르 진단을 만든다. 호출하는 쪽이 비용·취소를 처리한다.

    ``add_usage`` / ``check_cost`` / ``invoke_parsed``는 runner의 같은 이름 함수다
    (runner가 이 모듈을 불러오므로 순환 import를 피하려고 넘겨 받는다).
    """
    project = _project_row(conn, project_id)
    paras = _para_map(paragraphs)
    promise_broken = _text(project.get("promise_broken"), 800)
    promise_kept = _text(project.get("promise_kept"), 800)
    needed = required_keys(project.get("main_genre"), has_kept_promise=bool(promise_kept))
    guidance = _text(project.get("tory_priority_md"), 800) or "(없음)"
    # invoke_parsed는 runner의 공통 시스템(system_core)을 쓰므로, 장르 지침은 단계 프롬프트 앞에 붙인다.
    genre_rules = prompt_loader.load_text("genre/puzzle_system.txt").strip()

    def call(prompt: str, schema_name: str, label: str) -> dict[str, Any]:
        return invoke_parsed(
            caller,
            genre_rules + "\n\n" + prompt,
            prompt_loader.load_json(schema_name),
            model,
            cached_prefix=shared_prefix,
            label=label,
        )

    map_prompt = prompt_loader.fill_template(
        prompt_loader.load_text("genre/puzzle_clue_map_prompt.txt"), {}
    )
    map_result = call(map_prompt, "genre/puzzle_clue_map_schema.json", "정보 공개 지도")
    add_usage(params, "genre_clue_map", map_result, model)
    check_cost(params, max_cost)
    if map_result.get("parse_failed"):
        raise ValueError("정보 공개 지도를 읽지 못했어요.")
    clue_map, dropped = normalize_clue_map(map_result.get("parsed"), paras)

    required_line = "필수 항목: " + ", ".join(needed)
    rubric_vars = {
        "emphasis": _emphasis_text(project),
        "promise_broken": promise_broken or "(비어 있음)",
        "promise_kept": promise_kept or "(비어 있음)",
        "required_keys": required_line,
        "guidance": guidance,
        "clue_map": json.dumps(clue_map, ensure_ascii=False),
    }
    template = prompt_loader.load_text("genre/puzzle_rubric_prompt.txt")
    rubric_result = call(
        prompt_loader.fill_template(template, rubric_vars), "genre/puzzle_rubric_schema.json", "장르 진단"
    )
    add_usage(params, "genre_rubric", rubric_result, model)
    check_cost(params, max_cost)
    if rubric_result.get("parse_failed"):
        raise ValueError("장르 진단을 읽지 못했어요.")
    rubric, more = normalize_rubric(rubric_result.get("parsed"), paras, needed=needed)
    dropped.extend(more)

    absent = missing_keys(rubric)
    if absent:
        rubric_vars["required_keys"] = required_line + "\n빠진 항목을 반드시 채워라: " + ", ".join(absent)
        retry = call(
            prompt_loader.fill_template(template, rubric_vars), "genre/puzzle_rubric_schema.json", "장르 진단"
        )
        add_usage(params, "genre_rubric_retry", retry, model)
        check_cost(params, max_cost)
        if not retry.get("parse_failed"):
            retried, more = normalize_rubric(retry.get("parsed"), paras, needed=needed)
            dropped.extend(more)
            if len(missing_keys(retried)) < len(absent):
                rubric = retried

    return {
        "group": GROUP,
        "main_genre": str(project.get("main_genre") or ""),
        "sub_genre": str(project.get("sub_genre") or ""),
        "promise": {"broken": bool(promise_broken), "kept": bool(promise_kept)},
        "clue_map": clue_map,
        **rubric,
        "dropped": dropped,
    }


# ── 4단계: 장르 카드와 검증 ───────────────────────────────────────────────

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

INTENT_OK_WARNING = {
    "code": "genre_intent_ok",
    "severity": "info",
    "message": "의도한 설계라면 그대로 두셔도 돼요",
}
CLUE_TOUCH_WARNING = {
    "code": "clue_touch",
    "severity": "info",
    "message": "단서나 반전에 닿는 부분이라 수정안 대신 지적만 드려요",
}


def broken_promise_text(conn: sqlite3.Connection, project_id: int) -> str:
    return _text(_project_row(conn, project_id).get("promise_broken"), 800)


def promise_covers(text: str, promise: str) -> bool:
    """카드(원문+이유)가 작가가 「일부러 깬 약속」으로 적은 것과 같은 이야기인가.

    칸에 적은 것 자체가 의도 표시이므로 별도 의도 신호어는 요구하지 않는다.
    문학 쪽 거름과 같은 도구를 쓴다(구절이 그대로 들어 있거나 내용어가 2개 이상 겹침).
    """
    from feedback_pipeline.literature_cards import _choice_hits, _content_tokens

    source = str(promise or "").strip()
    if not source or not str(text or "").strip():
        return False
    if _choice_hits(text, source):
        return True
    return len(_content_tokens(source) & _content_tokens(text)) >= 2


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


def strip_suggestion_near_clues(card: dict[str, Any], clue_paras: set[int]) -> bool:
    """수정안 카드가 단서·반전 문단에 닿으면 수정안을 지우고 지적만 남긴다. 바꿨으면 True."""
    if not clue_paras or not card.get("suggestion"):
        return False
    start = _int(card.get("start_para"))
    end = _int(card.get("end_para"), start)
    if not any(start <= para <= max(start, end) for para in clue_paras):
        return False
    card["suggestion"] = None
    card["edit_plan"] = ""
    warnings = list(card.get("warnings_json") or [])
    warnings.append(dict(CLUE_TOUCH_WARNING))
    card["warnings_json"] = warnings
    return True


def card_impact(key: str, verdict: str) -> int:
    base = 5 if verdict == "problem" else 3
    if verdict == "room" and key in _CORE_KEYS:
        base = 4
    return base


def build_genre_cards(
    genre: dict[str, Any] | None,
    paragraphs: list[Paragraph],
    scene_id: int,
    *,
    promise_broken: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """장르 진단에서 지적형 카드를 만든다. (카드, 버린 항목)을 돌려준다.

    - 보완·문제 판정에 원고에서 확인된 근거가 있을 때만 만든다.
    - 수정안은 없다. 단서·반전에 닿는 문제는 항상 지적형이다.
    - 작가가 「일부러 깬 약속」으로 적은 것과 같은 이야기는 만들지 않는다.
    """
    from feedback_pipeline.paragraphs import slice_original

    cards: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    if not isinstance(genre, dict):
        return cards, dropped
    for item in genre.get("items") or []:
        key = str(item.get("key") or "")
        verdict = str(item.get("verdict") or "")
        tag = CARD_TAGS.get(key)
        if tag is None or verdict not in {"room", "problem"}:
            continue
        ref = f"GENRE:{key}"
        label = CARD_LABELS[tag]
        evidence = [row for row in item.get("evidence") or [] if isinstance(row, dict)]
        if not evidence:
            dropped.append({"id": ref, "title": label, "reason": "no_evidence"})
            continue
        first = evidence[0]
        para = _int(first.get("para"))
        quote = str(first.get("quote") or "")
        note = str(item.get("note") or "")
        if promise_covers(f"{quote}\n{label}\n{note}", promise_broken):
            dropped.append({"id": ref, "title": label, "reason": "author_promise"})
            continue
        original = slice_original(paragraphs, para, para, quote, quote) or quote
        cards.append(
            {
                "scene_id": scene_id,
                "kind": "structure",
                "style_type": tag,
                "impact": card_impact(key, verdict),
                "start_para": para,
                "end_para": para,
                "start_quote": quote,
                "end_quote": quote,
                "original_text": original,
                "reason": note,
                "edit_plan": "",
                "suggestion": None,
                "report_ref": ref,
                "perspectives_json": ["editor"],
                "title": label,
                "warnings_json": [
                    {
                        "code": "note_only",
                        "message": "수정안 없이 위치와 이유만 보여드리는 참고 지적이에요",
                    },
                    dict(INTENT_OK_WARNING),
                ],
            }
        )
    return cards, dropped


def render_genre_md(genre: dict[str, Any] | None) -> str:
    """리포트 마크다운 뒤에 붙는 장르 진단 글."""
    if not isinstance(genre, dict) or not genre.get("items"):
        return ""
    labels = {
        "clue_fairness": "단서의 공정성",
        "disclosure_order": "정보 공개 순서",
        "payoff": "복선·의문 회수",
        "twist_plausibility": "반전의 개연성",
        "tension_curve": "긴장 곡선",
        "threat_motive": "위협·동기 일관성",
        "deception": "속임수",
        "kept_promise": "지키고 싶은 약속",
    }
    verdict = {"works": "잘 작동함", "room": "보완 여지", "problem": "문제", "none": "해당 없음"}
    parts = ["## 장르 진단 (수수께끼형)"]
    summary = str(genre.get("summary") or "").strip()
    if summary:
        parts.append(summary)
    for item in genre["items"]:
        mark = " · 의도라면 유지" if item.get("intentional") else ""
        parts.append(f"### {labels.get(item['key'], item['key'])} — {verdict.get(item['verdict'], '')}{mark}")
        if item.get("note"):
            parts.append(str(item["note"]))
        for ev in item.get("evidence") or []:
            parts.append(f"- (P{ev['para']}) {ev['quote']}")
    tasks = genre.get("top_tasks") or []
    if tasks:
        parts.append("### 우선 퇴고 과제")
        parts.extend(f"- {task['title']}: {task.get('why') or ''}" for task in tasks)
    return "\n\n".join(parts) + "\n"
