"""장르문학 장르 레이어(수수께끼형·세계형)가 함께 쓰는 도구.

장르 레이어는 웹소설 러너(``runner.run_feedback``) 안에서 두 번 부른다.
1) 장르별 지도(정보 공개 지도 / 설정 지도)  2) 장르 진단 루브릭(총평·강점·우선 과제 포함).
결과는 ``report["genre"]``에 붙고, 보완·문제 판정은 지적형 카드가 된다(수정안 없음).

설계는 docs/genre-pipeline.md를 따른다. 묶음마다 다른 것(프롬프트, 항목, 카드 유형)은
``LayerSpec``과 각 묶음 모듈(genre_puzzle, genre_world)에 둔다.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from feedback_pipeline import prompt_loader
from feedback_pipeline.paragraphs import Paragraph, find_in_paragraph

VERDICTS = ("works", "room", "problem", "none")

INTENT_OK_WARNING = {
    "code": "genre_intent_ok",
    "severity": "info",
    "message": "의도한 설계라면 그대로 두셔도 돼요",
}
NOTE_ONLY = {
    "code": "note_only",
    "message": "수정안 없이 위치와 이유만 보여드리는 참고 지적이에요",
}


def text(value: object, limit: int = 0) -> str:
    out = " ".join(str(value or "").split())
    return out[:limit] if limit else out


def to_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def para_map(paragraphs: list[Paragraph]) -> dict[int, Paragraph]:
    return {int(p.number): p for p in paragraphs}


def quote_in_manuscript(paras: dict[int, Paragraph], para: int, quote: str) -> bool:
    """원고 문단 안에 인용이 실제로 있는가. 문단 번호가 틀려도 근처 문단에서 찾으면 인정하지 않는다."""
    target = paras.get(int(para))
    if target is None or not str(quote or "").strip():
        return False
    return find_in_paragraph(target, quote) is not None


def clean_evidence(rows: Any, paras: dict[int, Paragraph]) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    removed = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        para = to_int(row.get("para"))
        quote = text(row.get("quote"))
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
    valid_keys: tuple[str, ...],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """루브릭 판정을 정리한다.

    - 원고에 없는 인용은 지운다. 근거가 하나도 안 남은 보완·문제 판정은 ``unverified``로 표시한다.
    - 의도적(intentional)인 problem은 room으로 낮춘다. 의도를 존중하되 전달이 약하면 알려 준다.
    - 필요한데 빠진 항목은 none으로 채운다(``missing``). 필요하지 않은 항목은 버린다.
    """
    data = raw if isinstance(raw, dict) else {}
    dropped: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key not in valid_keys or key in by_key:
            continue
        verdict = str(item.get("verdict") or "none")
        if verdict not in VERDICTS:
            verdict = "none"
        intentional = bool(item.get("intentional"))
        evidence, removed = clean_evidence(item.get("evidence"), paras)
        if removed:
            dropped.append({"where": f"rubric.{key}", "reason": "quote_invalid", "count": removed})
        if intentional and verdict == "problem":
            verdict = "room"
        by_key[key] = {
            "key": key,
            "verdict": verdict,
            "intentional": intentional,
            "note": text(item.get("note"), 200),
            "evidence": evidence,
            "unverified": verdict in {"room", "problem"} and not evidence,
        }
    items: list[dict[str, Any]] = []
    for key in valid_keys:
        if key not in needed:
            continue
        if key in by_key:
            items.append(by_key[key])
        else:
            items.append(
                {"key": key, "verdict": "none", "intentional": False, "note": "", "evidence": [],
                 "unverified": False, "missing": True}
            )

    strengths: list[dict[str, Any]] = []
    for item in data.get("strengths") or []:
        if not isinstance(item, dict):
            continue
        para = to_int(item.get("para"))
        quote = text(item.get("quote"))
        if not text(item.get("title")) or not quote_in_manuscript(paras, para, quote):
            dropped.append({"where": "rubric.strengths", "reason": "quote_invalid", "para": para})
            continue
        strengths.append(
            {"title": text(item.get("title"), 60), "body": text(item.get("body"), 200), "para": para, "quote": quote}
        )
    tasks = [
        {"title": text(item.get("title"), 80), "why": text(item.get("why"), 200)}
        for item in (data.get("top_tasks") or [])
        if isinstance(item, dict) and text(item.get("title"))
    ]
    return (
        {
            "summary": text(data.get("summary"), 500),
            "items": items,
            "strengths": strengths[:3],
            "top_tasks": tasks[:3],
        },
        dropped,
    )


def missing_keys(rubric: dict[str, Any]) -> list[str]:
    return [item["key"] for item in rubric.get("items") or [] if item.get("missing")]


def playbook_emphasis_text(
    project: dict[str, Any],
    *,
    heading: str,
    detail_labels: dict[str, str] | None = None,
) -> str:
    """AI 패널 판단 기준(A그룹)과 같은 기준집에서 첨삭 루브릭에 넣을 글을 뽑는다.

    도우미 설정 검사와 말이 어긋나지 않게, 점검·판정 지침·세부장르 추가 기준은
    자르지 않고 넣는다. 톤·피할 것도 같이 넣되 세부장르의 피할 것·판정 지침을 빠뜨리지 않는다.
    로맨스는 도우미와 같은 캐릭터/관계 참고 어휘도 붙인다.
    """
    try:
        import app
        import genre_clusters
    except ImportError:  # CLI 등 앱 없이 쓸 때
        return ""
    main_genre = str(project.get("main_genre") or "")
    sub_genre = str(project.get("sub_genre") or "")
    detail = str(project.get("genre_detail") or "")
    book = app.load_genre_playbook(main_genre, sub_genre)
    if not isinstance(book, dict):
        return ""
    rules = book.get("group_rules") if isinstance(book.get("group_rules"), dict) else {}
    lines = [heading]
    for label, value in (
        ("톤", book.get("tone")),
        ("점검", book.get("checklist")),
        ("피할 것", book.get("avoid")),
        ("판정 지침", rules.get("A_judge")),
    ):
        blob = str(value or "").strip()
        if blob:
            lines.append(f"- {label}: {blob}")
    delta = app.load_genre_playbook_delta(main_genre, sub_genre, detail)
    if isinstance(delta, dict):
        _main, _sub, resolved = genre_clusters.playbook_lookup_keys(main_genre, sub_genre, detail)
        labels = dict(detail_labels or {})
        detail_label = labels.get(resolved) or genre_clusters.genre_detail_label(resolved) or resolved or "세부 장르"
        addition = str(delta.get("checklist_addition") or delta.get("checklist") or "").strip()
        avoid_extra = str(delta.get("avoid_addition") or delta.get("avoid") or "").strip()
        d_rules = app._playbook_delta_rules(delta)
        judge_extra = str(d_rules.get("A_judge") or "").strip() if isinstance(d_rules, dict) else ""
        if addition:
            lines.append(f"- {detail_label} 추가 점검: {addition}")
        if avoid_extra:
            lines.append(f"- {detail_label} 피할 것: {avoid_extra}")
        if judge_extra:
            lines.append(f"- {detail_label} 판정 지침: {judge_extra}")
    tags = app.format_genre_playbook_reference_tags_section(main_genre, book)
    if tags:
        lines.append(tags)
    return "\n".join(lines) if len(lines) > 1 else ""


def project_row(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT cluster_id, main_genre, sub_genre, genre_detail, tory_priority_md, "
        "promise_broken, promise_kept, cliche_used, cliche_avoid, romance_structure, romance_setting "
        "FROM project WHERE id = ?",
        (int(project_id),),
    ).fetchone()
    return dict(row) if row is not None else {}


def broken_promise_text(conn: sqlite3.Connection, project_id: int, field: str = "promise_broken") -> str:
    """작가가 「일부러 깬」 쪽 칸의 글. 묶음마다 칸 이름이 다르면 ``field``로 고른다

    (장르문학은 ``promise_broken``, 웹소설은 ``cliche_used`` — ``LayerSpec.broken_field``).
    """
    return text(project_row(conn, project_id).get(field), 800)


def promise_covers(blob: str, promise: str) -> bool:
    """카드(원문+이유)가 작가가 「일부러 깬 약속」으로 적은 것과 같은 이야기인가.

    칸에 적은 것 자체가 의도 표시이므로 별도 의도 신호어는 요구하지 않는다.
    문학 쪽 거름과 같은 도구를 쓴다(구절이 그대로 들어 있거나 내용어가 2개 이상 겹침).
    """
    from feedback_pipeline.literature_cards import _choice_hits, _content_tokens

    source = str(promise or "").strip()
    if not source or not str(blob or "").strip():
        return False
    if _choice_hits(blob, source):
        return True
    return len(_content_tokens(source) & _content_tokens(blob)) >= 2


def strip_suggestion_near(card: dict[str, Any], protected: set[int], warning: dict[str, Any]) -> bool:
    """수정안 카드가 보호할 문단(단서·반전·세계 규칙)에 닿으면 수정안을 지우고 지적만 남긴다."""
    if not protected or not card.get("suggestion"):
        return False
    start = to_int(card.get("start_para"))
    end = to_int(card.get("end_para"), start)
    if not any(start <= para <= max(start, end) for para in protected):
        return False
    card["suggestion"] = None
    card["edit_plan"] = ""
    warnings = list(card.get("warnings_json") or [])
    warnings.append(dict(warning))
    card["warnings_json"] = warnings
    return True


def card_impact(key: str, verdict: str, core_keys: set[str]) -> int:
    base = 5 if verdict == "problem" else 3
    if verdict == "room" and key in core_keys:
        base = 4
    return base


def build_cards(
    genre: dict[str, Any] | None,
    paragraphs: list[Paragraph],
    scene_id: int,
    *,
    promise_broken: str,
    tags: dict[str, str],
    labels: dict[str, str],
    core_keys: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """장르 진단에서 지적형 카드를 만든다. (카드, 버린 항목)을 돌려준다.

    - 보완·문제 판정에 원고에서 확인된 근거가 있을 때만 만든다.
    - 수정안은 없다. 단서·반전·세계 규칙에 닿는 문제는 항상 지적형이다.
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
        tag = tags.get(key)
        if tag is None or verdict not in {"room", "problem"}:
            continue
        ref = f"GENRE:{key}"
        label = labels[tag]
        evidence = [row for row in item.get("evidence") or [] if isinstance(row, dict)]
        if not evidence:
            dropped.append({"id": ref, "title": label, "reason": "no_evidence"})
            continue
        first = evidence[0]
        para = to_int(first.get("para"))
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
                "impact": card_impact(key, verdict, core_keys),
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
                "warnings_json": [dict(NOTE_ONLY), dict(INTENT_OK_WARNING)],
            }
        )
    return cards, dropped


@dataclass(frozen=True)
class LayerSpec:
    """묶음마다 다른 부분. 두 번 부르는 흐름은 ``run_report``가 같다."""

    group: str
    system_file: str
    map_prompt_file: str
    map_schema_file: str
    rubric_prompt_file: str
    rubric_schema_file: str
    map_label: str  # 오류 문구에 쓰는 이름 (예: 정보 공개 지도)
    map_key: str  # report.genre 안의 키 (예: clue_map)
    usage_stage: str  # 비용 기록 이름 접두 (예: genre_clue_map)
    normalize_map: Callable[[Any, dict[int, Paragraph]], tuple[dict[str, Any], list[dict[str, Any]]]]
    valid_keys: tuple[str, ...]
    required_keys: Callable[[dict[str, Any], bool], list[str]]
    emphasis: Callable[[dict[str, Any]], str]
    # 「일부러 쓴/깬」, 「지키고 싶은」 칸의 project 컬럼 이름. 장르문학은 기본값
    # (promise_broken/promise_kept) 그대로, 웹소설은 묶음 모듈의 SPEC에서
    # cliche_used/cliche_avoid로 바꿔 쓴다(docs/webnovel-genre-pipeline.md).
    broken_field: str = "promise_broken"
    kept_field: str = "promise_kept"


def run_report(
    spec: LayerSpec,
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
    """장르별 지도와 장르 진단을 만든다. 호출하는 쪽이 비용·취소를 처리한다.

    ``add_usage`` / ``check_cost`` / ``invoke_parsed``는 runner의 같은 이름 함수다
    (runner가 이 모듈을 불러오므로 순환 import를 피하려고 넘겨 받는다).
    """
    project = project_row(conn, project_id)
    paras = para_map(paragraphs)
    promise_broken = text(project.get(spec.broken_field), 800)
    promise_kept = text(project.get(spec.kept_field), 800)
    needed = spec.required_keys(project, bool(promise_kept))
    guidance = text(project.get("tory_priority_md"), 800) or "(없음)"
    # invoke_parsed는 runner의 공통 시스템(system_core)을 쓰므로, 장르 지침은 단계 프롬프트 앞에 붙인다.
    genre_rules = prompt_loader.load_text(spec.system_file).strip()

    def call(prompt: str, schema_name: str, label: str) -> dict[str, Any]:
        return invoke_parsed(
            caller,
            genre_rules + "\n\n" + prompt,
            prompt_loader.load_json(schema_name),
            model,
            cached_prefix=shared_prefix,
            label=label,
        )

    map_prompt = prompt_loader.fill_template(prompt_loader.load_text(spec.map_prompt_file), {})
    map_result = call(map_prompt, spec.map_schema_file, spec.map_label)
    add_usage(params, spec.usage_stage, map_result, model)
    check_cost(params, max_cost)
    if map_result.get("parse_failed"):
        raise ValueError(f"{spec.map_label}를 읽지 못했어요.")
    genre_map, dropped = spec.normalize_map(map_result.get("parsed"), paras)

    required_line = "필수 항목: " + ", ".join(needed)
    rubric_vars = {
        "emphasis": spec.emphasis(project),
        "promise_broken": promise_broken or "(비어 있음)",
        "promise_kept": promise_kept or "(비어 있음)",
        "required_keys": required_line,
        "guidance": guidance,
        spec.map_key: json.dumps(genre_map, ensure_ascii=False),
    }
    template = prompt_loader.load_text(spec.rubric_prompt_file)
    rubric_result = call(
        prompt_loader.fill_template(template, rubric_vars), spec.rubric_schema_file, "장르 진단"
    )
    add_usage(params, "genre_rubric", rubric_result, model)
    check_cost(params, max_cost)
    if rubric_result.get("parse_failed"):
        raise ValueError("장르 진단을 읽지 못했어요.")
    rubric, more = normalize_rubric(
        rubric_result.get("parsed"), paras, needed=needed, valid_keys=spec.valid_keys
    )
    dropped.extend(more)

    absent = missing_keys(rubric)
    if absent:
        rubric_vars["required_keys"] = required_line + "\n빠진 항목을 반드시 채워라: " + ", ".join(absent)
        retry = call(
            prompt_loader.fill_template(template, rubric_vars), spec.rubric_schema_file, "장르 진단"
        )
        add_usage(params, "genre_rubric_retry", retry, model)
        check_cost(params, max_cost)
        if not retry.get("parse_failed"):
            retried, more = normalize_rubric(
                retry.get("parsed"), paras, needed=needed, valid_keys=spec.valid_keys
            )
            dropped.extend(more)
            if len(missing_keys(retried)) < len(absent):
                rubric = retried

    return {
        "group": spec.group,
        "main_genre": str(project.get("main_genre") or ""),
        "sub_genre": str(project.get("sub_genre") or ""),
        "promise": {"broken": bool(promise_broken), "kept": bool(promise_kept)},
        spec.map_key: genre_map,
        **rubric,
        "dropped": dropped,
    }
