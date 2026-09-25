"""첨삭 피드백 실행. 단계마다 feedback_store에 즉시 저장한다."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

import feedback_store
from feedback_pipeline import prompt_loader
from feedback_pipeline.checks import (
    MAX_RANGE_SPAN,
    apply_card_priorities,
    check_item_range_quotes,
    check_leak_terms,
    check_v3_unknown_proper_nouns,
    check_v4_outside_sentence_copy,
    check_v6_length,
    check_v8_missing_terms,
    check_v9_repeat_and_pronoun,
    classify_v8_warning,
    relocate_item_range,
    warning_from_check,
)
from feedback_pipeline.claude_client import (
    ClaudeError,
    estimate_cost_usd,
    get_client,
    is_fake_mode,
)
from feedback_pipeline.config import (
    CARD_CONCURRENCY,
    FEEDBACK_MODEL,
    MAX_CARDS_PER_RUN,
    MAX_COST_USD_PER_RUN,
    PROMPT_VERSION,
    SUGGESTION_ENABLED_TYPES,
)
from feedback_pipeline.context import build_project_context, explanation_lens_for_project
from feedback_pipeline.dup_blocks import dup_reason, find_dup_blocks, format_dup_findings
from feedback_pipeline.paragraphs import (
    RangeHit,
    context_numbers,
    format_para_block,
    format_paragraphs_text,
    in_paragraph_outside,
    locate_quote_range,
    slice_original,
    source_hash,
    to_paragraphs,
)
from feedback_pipeline.report_post import apply_report_post, filter_p2_items


class CostLimitExceeded(RuntimeError):
    """누적 추정 비용이 한도를 넘었다."""


class Cancelled(RuntimeError):
    """실행 중 취소 플래그가 세워졌다."""


def _now(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0])


def _loads_params(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT params_json FROM feedback_run WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return {}
    try:
        data = json.loads(row[0] or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _save_params(conn: sqlite3.Connection, run_id: int, params: dict[str, Any]) -> None:
    feedback_store.update_run(conn, run_id, params_json=params)


def _commit(conn: sqlite3.Connection, options: dict[str, Any]) -> None:
    if options.get("autocommit"):
        conn.commit()


def _raise_if_cancelled(cancel_event: Any) -> None:
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise Cancelled("취소되었습니다.")


def _set_progress(
    conn: sqlite3.Connection,
    run_id: int,
    params: dict[str, Any],
    stage: str,
    done: int,
    total: int,
) -> None:
    params["progress"] = {"stage": stage, "done": int(done), "total": int(total)}
    _save_params(conn, run_id, params)


def _add_usage(
    params: dict[str, Any],
    stage: str,
    result: dict[str, Any] | None,
    model: str,
) -> float:
    from feedback_pipeline.claude_client import estimate_cost_parts

    usage = (result or {}).get("usage") or {}
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cache_write = int(usage.get("cache_creation_input_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    parts = estimate_cost_parts(
        model, inp, out, cache_write_tokens=cache_write, cache_read_tokens=cache_read
    )
    cost = float(parts["total_usd"])
    bucket = params.setdefault("usage", {})
    stages = bucket.setdefault("stages", [])
    stages.append(
        {
            "stage": stage,
            "model": model,
            "input_tokens": inp,
            "output_tokens": out,
            "cache_creation_input_tokens": cache_write,
            "cache_read_input_tokens": cache_read,
            "cost_input_usd": parts["input_usd"],
            "cost_cache_write_usd": parts["cache_write_usd"],
            "cost_cache_read_usd": parts["cache_read_usd"],
            "cost_output_usd": parts["output_usd"],
            "cost_usd": round(cost, 6),
        }
    )
    bucket["input_tokens"] = int(bucket.get("input_tokens") or 0) + inp
    bucket["output_tokens"] = int(bucket.get("output_tokens") or 0) + out
    bucket["cache_creation_input_tokens"] = int(bucket.get("cache_creation_input_tokens") or 0) + cache_write
    bucket["cache_read_input_tokens"] = int(bucket.get("cache_read_input_tokens") or 0) + cache_read
    bucket["cost_input_usd"] = round(float(bucket.get("cost_input_usd") or 0) + parts["input_usd"], 6)
    bucket["cost_cache_write_usd"] = round(
        float(bucket.get("cost_cache_write_usd") or 0) + parts["cache_write_usd"], 6
    )
    bucket["cost_cache_read_usd"] = round(
        float(bucket.get("cost_cache_read_usd") or 0) + parts["cache_read_usd"], 6
    )
    bucket["cost_output_usd"] = round(float(bucket.get("cost_output_usd") or 0) + parts["output_usd"], 6)
    bucket["cost_usd"] = round(float(bucket.get("cost_usd") or 0) + cost, 6)
    return float(bucket["cost_usd"])


def _check_cost(params: dict[str, Any], limit: float) -> None:
    cost = float((params.get("usage") or {}).get("cost_usd") or 0)
    if cost > float(limit):
        raise CostLimitExceeded(f"추정 비용 ${cost:.4f}가 한도 ${limit:.2f}를 넘었습니다.")


def _range_of(item: dict[str, Any]) -> dict[str, Any] | None:
    rng = item.get("range")
    return rng if isinstance(rng, dict) else None


NOTE_ONLY_WARNING = {
    "code": "note_only",
    "message": "수정안 없이 위치와 이유만 보여드리는 참고 지적이에요",
}


def _item_id(item: dict[str, Any] | None) -> str:
    return str((item or {}).get("id") or "")


def _drop(params: dict[str, Any], item: dict[str, Any] | None, reason: str) -> None:
    """params.dropped에 id/title/reason을 남긴다."""
    row_item = item if isinstance(item, dict) else {}
    params.setdefault("dropped", []).append(
        {
            "id": _item_id(row_item),
            "title": str(row_item.get("title") or ""),
            "reason": str(reason),
        }
    )


def _relocated_item(item: dict[str, Any], paragraphs) -> dict[str, Any]:
    out = dict(item)
    relocated = relocate_item_range(out, paragraphs)
    if relocated is not None:
        out["range"] = relocated
    return out


def _range_check(
    item: dict[str, Any], paragraphs
) -> tuple[dict[str, Any], dict[str, Any]]:
    prepared = _relocated_item(item, paragraphs)
    return prepared, check_item_range_quotes(prepared, paragraphs)


def _clip_hit_span(
    hit: RangeHit, paragraphs, max_span: int = MAX_RANGE_SPAN
) -> RangeHit:
    """밑줄용으로 구간을 앞쪽 max_span 문단만 남긴다."""
    start = int(hit.start_para or 0)
    end = int(hit.end_para or start)
    if start <= 0 or end < start or (end - start + 1) <= max_span:
        return hit
    new_end = start + max_span - 1
    last = next((para for para in paragraphs if para.number == new_end), None)
    end_quote = ""
    if last is not None:
        text = str(last.text or "").strip() or str(last.text or "")
        end_quote = text[-24:] if text else ""
    original = slice_original(
        paragraphs, start, new_end, hit.start_quote, end_quote
    )
    return RangeHit(
        start_para=start,
        end_para=new_end,
        original_text=original or hit.original_text,
        start_quote=hit.start_quote,
        end_quote=end_quote or hit.end_quote,
    )


def _impact_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _format_consistency_sure(issues: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for issue in issues:
        if str(issue.get("certainty") or "").strip() != "sure":
            continue
        title = str(issue.get("title") or "").strip() or "(제목 없음)"
        rng = _range_of(issue) or {}
        start = rng.get("start_para")
        end = rng.get("end_para")
        if start is None:
            loc = "?"
        elif end is None or end == start:
            loc = f"P{start}"
        else:
            loc = f"P{start}~P{end}"
        lines.append(f"- 정합성: ({title}) ({loc}) [확실]")
    return "\n".join(lines)


def _merge_findings(dup_text: str, consistency_text: str) -> str:
    parts: list[str] = []
    dup = (dup_text or "").strip()
    if dup and dup != "없음":
        parts.append(dup)
    cons = (consistency_text or "").strip()
    if cons:
        parts.append(cons)
    return "\n".join(parts) if parts else "없음"


def _hit_from_item(paragraphs, item: dict[str, Any]) -> RangeHit | None:
    rng = _range_of(item)
    if not rng:
        return None
    start_q = str(rng.get("start_quote") or "")
    end_q = str(rng.get("end_quote") or "")
    start_p = rng.get("start_para")
    end_p = rng.get("end_para")
    try:
        start_p = int(start_p)
        end_p = int(end_p)
    except (TypeError, ValueError):
        return locate_quote_range(paragraphs, start_q, end_q)
    original = slice_original(paragraphs, start_p, end_p, start_q, end_q)
    return RangeHit(
        start_para=start_p,
        end_para=end_p,
        original_text=original,
        start_quote=start_q,
        end_quote=end_q,
    )


def _suggestion_enabled(kind: str, style_type: str) -> bool:
    if kind in {"correction", "consistency"}:
        return True
    return str(style_type or "") in SUGGESTION_ENABLED_TYPES


def _strength_ranges_text(paragraphs, report: dict[str, Any] | None) -> str:
    lines: list[str] = []
    for item in (report or {}).get("strengths") or []:
        if not isinstance(item, dict):
            continue
        hit = _hit_from_item(paragraphs, item)
        if hit and hit.original_text.strip():
            lines.append(hit.original_text.strip())
    return "\n---\n".join(lines) if lines else "(없음)"


def render_report_md(report: dict[str, Any] | None) -> str:
    if not isinstance(report, dict):
        return ""
    parts: list[str] = []
    summary = str(report.get("summary") or "").strip()
    if summary:
        parts.append("## 요약")
        parts.append(summary)
        parts.append("")
    scores = report.get("scores") if isinstance(report.get("scores"), list) else []
    if scores:
        parts.append("## 점수")
        for row in scores:
            if not isinstance(row, dict):
                continue
            parts.append(f"- {row.get('item') or ''}: {row.get('score')} — {row.get('comment') or ''}")
        parts.append("")
    for title, key in (
        ("강점", "strengths"),
        ("약점", "weaknesses"),
        ("정합성", "consistency"),
    ):
        items = report.get(key) if isinstance(report.get(key), list) else []
        if not items:
            continue
        parts.append(f"## {title}")
        for item in items:
            if not isinstance(item, dict):
                continue
            rng = _range_of(item) or {}
            loc = ""
            if rng.get("start_para") is not None:
                loc = f" (P{rng.get('start_para')}~{rng.get('end_para')})"
            parts.append(f"### {item.get('title') or ''}{loc}")
            body = str(item.get("body") or "").strip()
            if body:
                parts.append(body)
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _project_terms_from_context(context: str) -> str:
    return context.strip() or "(없음)"


def _project_for_checks(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    people = []
    for row in conn.execute(
        "SELECT id, name FROM character WHERE project_id = ? AND deleted_at IS NULL",
        (project_id,),
    ).fetchall():
        aliases = [
            str(a[0])
            for a in conn.execute(
                "SELECT alias FROM character_alias WHERE character_id = ?",
                (int(row["id"]),),
            ).fetchall()
        ]
        people.append({"name": row["name"], "aliases": aliases})
    return {"characters": people, "terms": []}


def _invoke(claude, prompt: str, schema: dict[str, Any] | None, model: str) -> dict[str, Any]:
    return claude.generate(
        prompt,
        model=model,
        system=prompt_loader.system_core(),
        thinking="off",
        json_schema=schema,
        max_tokens=4096,
        timeout=60.0,
    )


def _build_card_prompt(
    *,
    paragraphs,
    item: dict[str, Any],
    hit: RangeHit,
    kind: str,
    style_type: str,
    report: dict[str, Any] | None,
    project_context: str,
    guidance: dict[str, Any],
) -> str:
    total = paragraphs[-1].number if paragraphs else 0
    target_nums = list(range(hit.start_para, hit.end_para + 1))
    context_nums = context_numbers(hit.start_para, hit.end_para, total)
    before_txt, after_txt = in_paragraph_outside(paragraphs, hit)
    enabled = _suggestion_enabled(kind, style_type)
    values = {
        "card_schema": prompt_loader.card_schema_text(),
        "item_id": str(item.get("id") or ""),
        "kind": kind,
        "style_type": style_type,
        "certainty": item.get("certainty") or "",
        "item_title": item.get("title") or "",
        "item_body": item.get("body") or "",
        "perspectives": ", ".join(str(p) for p in (item.get("perspectives") or [])),
        "suggestion_enabled": "true" if enabled else "false",
        "type_guidance": prompt_loader.type_guidance_text(guidance, style_type),
        "start_para": str(hit.start_para),
        "end_para": str(hit.end_para),
        "target_paragraphs_text": format_para_block(paragraphs, target_nums),
        "target_range_text": hit.original_text or "(없음)",
        "before_in_paragraph": before_txt,
        "after_in_paragraph": after_txt,
        "context_paragraphs_text": format_para_block(paragraphs, context_nums),
        "strength_ranges_text": _strength_ranges_text(paragraphs, report),
        "project_terms_text": _project_terms_from_context(project_context),
        "alternate_rule": "",
    }
    return prompt_loader.fill_template(prompt_loader.card_prompt(), values)


DUP_CARD_TITLE = "같은 내용이 반복된 문단"
_STYLE_TITLES = {
    "explain_less": "설명이 장면을 멈추게 해요",
    "info_placement": "정보가 이 자리에 없어서 읽기가 끊겨요",
    "action_clarity": "동작 순서가 겹쳐 보여요",
    "redundancy": "같은 정보가 반복돼요",
    "reader_question": "독자 의문이 남아요",
    "abstract_expression": "표현이 추상적이에요",
    "other": "손보면 좋은 문장",
}


def _card_title(item: dict[str, Any] | None, kind: str, style_type: str = "") -> str:
    title = str((item or {}).get("title") or "").strip()
    if title:
        return title
    if kind == "consistency":
        return "설정·인물이 어긋나 보여요"
    if kind == "structure":
        return "장면 구조를 손보면 좋겠어요"
    return _STYLE_TITLES.get(str(style_type or "").strip()) or _STYLE_TITLES["other"]


def _card_from_model(
    parsed: dict[str, Any] | None,
    *,
    item: dict[str, Any],
    hit: RangeHit,
    kind: str,
    style_type: str,
    project_id: int,
    scene_id: int,
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    parsed = parsed if isinstance(parsed, dict) else {}
    suggestion = parsed.get("suggestion")
    if isinstance(suggestion, str) and suggestion.strip().lower() == "null":
        suggestion = None
    impact = item.get("impact")
    try:
        impact = int(impact) if impact is not None else None
    except (TypeError, ValueError):
        impact = None
    certainty = str(item.get("certainty") or "") or None
    if kind == "consistency" and impact is None:
        impact = 5 if certainty == "sure" else 3
    confidence = parsed.get("confidence")
    if isinstance(confidence, str):
        confidence = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(
            confidence.strip().lower()
        )
    else:
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
    return {
        "scene_id": scene_id,
        "kind": kind,
        "style_type": style_type or "",
        "certainty": certainty,
        "impact": impact,
        "start_para": hit.start_para,
        "end_para": hit.end_para,
        "start_quote": hit.start_quote,
        "end_quote": hit.end_quote,
        "original_text": hit.original_text,
        "reason": str(parsed.get("reason") or item.get("body") or ""),
        "edit_plan": str(parsed.get("edit_plan") or ""),
        "suggestion": suggestion,
        "added_facts_json": parsed.get("added_facts") or [],
        "removed_facts_json": parsed.get("removed_facts") or [],
        "warnings_json": warnings,
        "report_ref": str(item.get("id") or ""),
        "perspectives_json": item.get("perspectives") or [],
        "confidence": confidence,
        "title": _card_title(item, kind, style_type),
    }


def _apply_card_rules(card: dict[str, Any], paragraphs, project: dict[str, Any]) -> dict[str, Any]:
    warnings = list(card.get("warnings_json") or [])
    suggestion = card.get("suggestion")
    removed = False
    v3 = check_v3_unknown_proper_nouns(
        suggestion=suggestion,
        original_text=card.get("original_text") or "",
        project=project,
    )
    if not v3.get("ok") and not v3.get("skipped"):
        card["suggestion"] = None
        suggestion = None
        removed = True
    v4 = check_v4_outside_sentence_copy(
        suggestion=suggestion,
        paragraphs=paragraphs,
        start_para=int(card.get("start_para") or 0),
        end_para=int(card.get("end_para") or 0),
        start_quote=str(card.get("start_quote") or ""),
        end_quote=str(card.get("end_quote") or ""),
    )
    if not v4.get("ok") and not v4.get("skipped"):
        card["suggestion"] = None
        suggestion = None
        removed = True
    if removed:
        warnings.append(
            {
                "code": "suggestion_removed",
                "message": "수정안이 원문에 없는 이름이나 문장을 포함해서 제거했어요",
            }
        )
    for result in (
        check_v6_length(
            suggestion=suggestion,
            original_text=card.get("original_text") or "",
            kind=str(card.get("kind") or ""),
        ),
        check_v9_repeat_and_pronoun(
            suggestion=suggestion,
            original_text=card.get("original_text") or "",
        ),
    ):
        warn = warning_from_check(result)
        if warn:
            warnings.append(warn)
    v8 = check_v8_missing_terms(
        suggestion=suggestion,
        original_text=card.get("original_text") or "",
        project=project,
    )
    v8_warn = classify_v8_warning(
        original_text=card.get("original_text") or "",
        suggestion=suggestion,
        result=v8,
    )
    if v8_warn:
        warnings.append(v8_warn)
    card["warnings_json"] = warnings
    return card


def _generate_one_card(claude, prompt: str, model: str) -> dict[str, Any]:
    last_error: Exception | None = None
    last_text = ""
    for _attempt in range(2):
        try:
            result = _invoke(claude, prompt, prompt_loader.CARD_JSON_SCHEMA, model)
            last_text = str(result.get("text") or "")
            parsed = result.get("parsed")
            if isinstance(parsed, dict):
                return result
            last_error = ClaudeError("카드 JSON 파싱 실패", code="empty")
        except ClaudeError as error:
            last_error = error
            last_text = str(error)
    return {
        "text": last_text,
        "parsed": None,
        "raw": last_text,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "parse_failed": True,
        "error": str(last_error) if last_error else "카드 생성 실패",
    }


def run_feedback(
    conn: sqlite3.Connection,
    project_id: int,
    scene_id: int,
    paragraphs: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
    claude: Any | None = None,
) -> int:
    """파이프라인을 돌리고 run_id를 반환한다.

    options.run_id가 있으면 그 실행을 이어서 쓰고, 없으면 새로 만든다.
    """
    options = dict(options or {})
    model = str(options.get("model") or FEEDBACK_MODEL)
    max_cost = float(options.get("max_cost") or MAX_COST_USD_PER_RUN)
    scene_title = str(options.get("scene_title") or "")
    revision_no = options.get("revision_no")
    cancel_event = options.get("cancel_event")
    caller = claude or get_client()
    paras = to_paragraphs(paragraphs)
    project_id = int(project_id)
    scene_id = int(scene_id)

    lens = options.get("explanation_lens")
    if not lens:
        row = conn.execute(
            "SELECT main_genre, sub_genre, genre_detail FROM project WHERE id = ?",
            (project_id,),
        ).fetchone()
        if row is not None:
            lens = explanation_lens_for_project(
                row["main_genre"], row["sub_genre"], row["genre_detail"]
            )
        else:
            lens = "normal"
    lens_map = prompt_loader.lens_text()
    lens_body = lens_map.get(str(lens)) or lens_map.get("normal") or ""
    project_context = build_project_context(conn, project_id)
    check_project = _project_for_checks(conn, project_id)
    guidance = prompt_loader.type_guidance()

    existing_id = options.get("run_id")
    if existing_id is not None:
        run_id = int(existing_id)
        params = _loads_params(conn, run_id)
        params.setdefault(
            "progress", {"stage": "start", "done": 0, "total": 5}
        )
        params.setdefault(
            "usage",
            {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "stages": []},
        )
        params.setdefault("dropped", [])
        if "fake" not in params:
            params["fake"] = bool(is_fake_mode())
        params["explanation_lens"] = lens
        _save_params(conn, run_id, params)
        _commit(conn, options)
    else:
        params = {
            "progress": {"stage": "start", "done": 0, "total": 5},
            "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "stages": []},
            "dropped": [],
            "explanation_lens": lens,
            "fake": bool(is_fake_mode()),
        }
        run_id = feedback_store.create_run(
            conn,
            project_id,
            "analyze",
            model,
            PROMPT_VERSION,
            params,
        )
        feedback_store.add_run_scene(
            conn,
            run_id,
            project_id,
            scene_id,
            0,
            scene_title,
            None if revision_no is None else int(revision_no),
            source_hash(paras),
            [p.as_dict() for p in paras],
        )
        _commit(conn, options)
    last_raw = ""
    failed_cards = 0
    report_failed = False
    cost_stopped = False

    try:
        _raise_if_cancelled(cancel_event)
        _set_progress(conn, run_id, params, "dup", 0, 5)
        _commit(conn, options)
        blocks = find_dup_blocks(paras)
        dup_cards = []
        for index, block in enumerate(blocks, start=1):
            a0, a1 = block["a"]
            dup_cards.append(
                {
                    "scene_id": scene_id,
                    "kind": "structure",
                    "style_type": "none",
                    "impact": 5,
                    "start_para": a0,
                    "end_para": a1,
                    "start_quote": "",
                    "end_quote": "",
                    "original_text": "",
                    "reason": dup_reason(block),
                    "edit_plan": "",
                    "suggestion": None,
                    "report_ref": f"DUP{index}",
                    "title": DUP_CARD_TITLE,
                }
            )
        if dup_cards:
            feedback_store.add_cards(conn, run_id, dup_cards)
        _set_progress(conn, run_id, params, "dup", 1, 5)
        _commit(conn, options)
        _raise_if_cancelled(cancel_event)

        _set_progress(conn, run_id, params, "consistency", 1, 5)
        _commit(conn, options)
        cons_prompt = prompt_loader.fill_template(
            prompt_loader.consistency_prompt(),
            {
                "project_context": project_context,
                "paragraphs_text": format_paragraphs_text(paras),
            },
        )
        cons_result = _invoke(caller, cons_prompt, prompt_loader.consistency_schema(), model)
        last_raw = str(cons_result.get("text") or "")
        _add_usage(params, "consistency", cons_result, model)
        _check_cost(params, max_cost)
        cons_parsed = cons_result.get("parsed")
        if not isinstance(cons_parsed, dict):
            cons_parsed = {}
        issues = [i for i in (cons_parsed.get("issues") or []) if isinstance(i, dict)]
        issues, p2_dropped = filter_p2_items(issues)
        for row in p2_dropped:
            _drop(params, row, "p2_filtered")
        kept_issues: list[dict[str, Any]] = []
        for issue in issues:
            prepared, quote_ok = _range_check(issue, paras)
            if not quote_ok.get("ok") and not quote_ok.get("null"):
                _drop(params, prepared, "quote_invalid")
                continue
            certainty = str(prepared.get("certainty") or "").strip()
            if prepared.get("impact") is None:
                prepared["impact"] = 5 if certainty == "sure" else 3
            kept_issues.append(prepared)
        params["consistency_issues"] = [
            {"title": i.get("title"), "certainty": i.get("certainty")} for i in kept_issues
        ]
        _save_params(conn, run_id, params)
        _set_progress(conn, run_id, params, "consistency", 2, 5)
        _commit(conn, options)
        _raise_if_cancelled(cancel_event)

        sure_text = _format_consistency_sure(kept_issues)
        findings = _merge_findings(format_dup_findings(blocks), sure_text)
        report_user = prompt_loader.fill_template(
            prompt_loader.report_prompt(),
            {
                "project_context": project_context,
                "rule_findings_text": findings,
                "explanation_lens": lens,
                "explanation_lens_text": lens_body,
                "paragraphs_text": format_paragraphs_text(paras),
                "report_schema": json.dumps(
                    prompt_loader.report_schema(), ensure_ascii=False
                ),
            },
        )
        params["report_prompt_findings"] = findings
        _save_params(conn, run_id, params)
        _set_progress(conn, run_id, params, "report", 2, 5)
        _commit(conn, options)
        _raise_if_cancelled(cancel_event)
        try:
            report_result = _invoke(caller, report_user, prompt_loader.report_schema(), model)
        except Exception:
            report_failed = True
            raise
        last_raw = str(report_result.get("text") or "")
        _add_usage(params, "report", report_result, model)
        _check_cost(params, max_cost)
        report = report_result.get("parsed")
        if not isinstance(report, dict):
            raise ClaudeError("리포트 JSON 파싱 실패", code="empty")
        report, post_changes = apply_report_post(report)
        params["post_changes"] = post_changes
        for row in post_changes:
            if row.get("step") == "P2":
                _drop(params, row, "p2_filtered")
        maybe_issues = [
            i for i in kept_issues if str(i.get("certainty") or "").strip() == "maybe"
        ]
        if maybe_issues and isinstance(report, dict):
            extra = list(report.get("consistency") or [])
            extra.extend(maybe_issues)
            report["consistency"] = extra
        leak = check_leak_terms(
            json.dumps(report, ensure_ascii=False) if report else ""
        )
        if leak.get("leak_count"):
            params["leak"] = leak
        quote_skip_ids: set[str] = set()
        cardable: list[tuple[str, str, dict[str, Any]]] = []
        for issue in kept_issues:
            prepared, quote_ok = _range_check(issue, paras)
            if not quote_ok.get("ok"):
                quote_skip_ids.add(str(prepared.get("id") or prepared.get("title") or ""))
                _drop(params, prepared, "quote_invalid")
                continue
            cardable.append(("consistency", "none", prepared))
        for item in (report or {}).get("weaknesses") or []:
            if not isinstance(item, dict):
                continue
            prepared, quote_ok = _range_check(item, paras)
            fixable = str(prepared.get("fixable") or "").strip()
            style_type = str(prepared.get("type") or "other")
            if fixable == "none":
                if quote_ok.get("null") or not quote_ok.get("ok"):
                    _drop(params, prepared, "range_invalid")
                    continue
                cardable.append(("style", style_type, prepared))
                continue
            if not quote_ok.get("ok") and not quote_ok.get("null"):
                _drop(params, prepared, "quote_invalid")
                continue
            if fixable == "sentence":
                cardable.append(("style", style_type, prepared))
            elif fixable == "structure":
                cardable.append(("structure", style_type, prepared))
            else:
                _drop(params, prepared, "not_cardable")
        if len(cardable) + len(blocks) > MAX_CARDS_PER_RUN:
            keep_n = max(0, MAX_CARDS_PER_RUN - len(blocks))
            for _kind, _style, extra in cardable[keep_n:]:
                _drop(params, extra, "cap_reached")
            cardable = cardable[:keep_n]
        planned = len(blocks) + len(cardable)
        feedback_store.update_run(
            conn,
            run_id,
            report_json=report,
            report_md=render_report_md(report),
            planned_cards=planned,
            params_json=params,
        )
        _set_progress(conn, run_id, params, "cards", 3, 5)
        _commit(conn, options)
        _raise_if_cancelled(cancel_event)

        ai_jobs: list[tuple[int, str, str, dict[str, Any], RangeHit, str]] = []
        instant_cards: list[dict[str, Any]] = []
        for kind, style_type, item in cardable:
            fixable = str(item.get("fixable") or "").strip()
            is_note = kind == "style" and fixable == "none"
            if kind == "structure" or is_note:
                hit = _hit_from_item(paras, item)
                if is_note:
                    if hit is None:
                        _drop(params, item, "no_original_range")
                        continue
                    hit = _clip_hit_span(hit, paras)
                    instant_cards.append(
                        {
                            "scene_id": scene_id,
                            "kind": "style",
                            "style_type": style_type,
                            "impact": _impact_int(item.get("impact")),
                            "start_para": hit.start_para,
                            "end_para": hit.end_para,
                            "start_quote": hit.start_quote,
                            "end_quote": hit.end_quote,
                            "original_text": hit.original_text,
                            "reason": str(item.get("body") or ""),
                            "edit_plan": "",
                            "suggestion": None,
                            "report_ref": str(item.get("id") or ""),
                            "perspectives_json": item.get("perspectives") or [],
                            "title": _card_title(item, "style", style_type),
                            "warnings_json": [dict(NOTE_ONLY_WARNING)],
                        }
                    )
                    continue
                hit = hit or RangeHit(
                    start_para=int((_range_of(item) or {}).get("start_para") or 0),
                    end_para=int((_range_of(item) or {}).get("end_para") or 0),
                    original_text=str(item.get("body") or ""),
                    start_quote=str((_range_of(item) or {}).get("start_quote") or ""),
                    end_quote=str((_range_of(item) or {}).get("end_quote") or ""),
                )
                instant_cards.append(
                    {
                        "scene_id": scene_id,
                        "kind": "structure",
                        "style_type": style_type,
                        "impact": item.get("impact"),
                        "start_para": hit.start_para,
                        "end_para": hit.end_para,
                        "start_quote": hit.start_quote,
                        "end_quote": hit.end_quote,
                        "original_text": hit.original_text,
                        "reason": str(item.get("body") or ""),
                        "edit_plan": "",
                        "suggestion": None,
                        "report_ref": str(item.get("id") or ""),
                        "perspectives_json": item.get("perspectives") or [],
                        "title": _card_title(item, "structure", style_type),
                    }
                )
                continue
            hit = _hit_from_item(paras, item)
            if hit is None:
                _drop(params, item, "no_original_range")
                continue
            prompt = _build_card_prompt(
                paragraphs=paras,
                item=item,
                hit=hit,
                kind=kind,
                style_type=style_type,
                report=report,
                project_context=project_context,
                guidance=guidance,
            )
            ai_jobs.append((len(ai_jobs), kind, style_type, item, hit, prompt))

        if instant_cards:
            feedback_store.add_cards(conn, run_id, instant_cards)
            _commit(conn, options)

        results: dict[int, dict[str, Any]] = {}
        if ai_jobs:
            workers = min(CARD_CONCURRENCY, len(ai_jobs))
            pending = list(ai_jobs)
            in_flight: dict[Any, tuple] = {}
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:

                def _submit_next() -> None:
                    if not pending:
                        return
                    if cancel_event is not None and getattr(
                        cancel_event, "is_set", lambda: False
                    )():
                        pending.clear()
                        return
                    job = pending.pop(0)
                    future = pool.submit(_generate_one_card, caller, job[5], model)
                    in_flight[future] = job

                for _ in range(min(workers, len(pending))):
                    _submit_next()
                while in_flight:
                    done, _not_done = wait(
                        list(in_flight), return_when=FIRST_COMPLETED
                    )
                    for future in done:
                        job = in_flight.pop(future)
                        index = job[0]
                        try:
                            results[index] = future.result()
                        except Exception as error:  # noqa: BLE001
                            results[index] = {
                                "parsed": None,
                                "parse_failed": True,
                                "error": str(error),
                                "text": "",
                                "usage": {"input_tokens": 0, "output_tokens": 0},
                            }
                        _submit_next()

        finished_indexes: set[int] = set()
        for index, kind, style_type, item, hit, _prompt in ai_jobs:
            if index not in results:
                continue
            result = results.get(index) or {}
            last_raw = str(result.get("text") or last_raw)
            _add_usage(params, f"card:{item.get('id') or index}", result, model)
            try:
                _check_cost(params, max_cost)
            except CostLimitExceeded:
                cost_stopped = True
                failed_cards += 1
                break
            warnings: list[dict[str, str]] = []
            parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else None
            if result.get("parse_failed") or parsed is None:
                failed_cards += 1
                _drop(params, item, "generation_failed")
                warnings.append({"code": "generation_failed", "message": "카드 JSON 파싱 실패"})
                parsed = {
                    "reason": str(item.get("body") or ""),
                    "suggestion": None,
                    "edit_plan": "",
                }
            card = _card_from_model(
                parsed,
                item=item,
                hit=hit,
                kind=kind,
                style_type=style_type,
                project_id=project_id,
                scene_id=scene_id,
                warnings=warnings,
            )
            card = _apply_card_rules(card, paras, check_project)
            feedback_store.add_cards(conn, run_id, [card])
            _commit(conn, options)
            finished_indexes.add(index)
            if cost_stopped:
                break
        for index, _kind, _style, item, _hit, _prompt in ai_jobs:
            if index not in finished_indexes:
                _drop(params, item, "generation_failed")

        _set_progress(conn, run_id, params, "priority", 4, 5)
        stored = conn.execute(
            "SELECT id, impact FROM feedback_card WHERE run_id = ? ORDER BY ord, id",
            (run_id,),
        ).fetchall()
        start_paras = {
            int(row["id"]): int(row_s["start_para"] or 0)
            for row in stored
            for row_s in conn.execute(
                "SELECT start_para FROM feedback_card WHERE id = ?", (int(row["id"]),)
            ).fetchall()
        }
        payload = [
            {
                "id": int(row["id"]),
                "impact": row["impact"],
                "start_para": start_paras.get(int(row["id"]), 0),
            }
            for row in stored
        ]
        ranked = apply_card_priorities(payload)
        mapping = {int(item["id"]): str(item.get("priority") or "low") for item in ranked}
        if mapping:
            feedback_store.update_card_priorities(conn, run_id, mapping)
        _commit(conn, options)
        _raise_if_cancelled(cancel_event)

        if cost_stopped:
            status = "partial"
        elif failed_cards:
            status = "partial"
        else:
            status = "ok"
        params["quote_skip_ids"] = sorted(quote_skip_ids)
        _set_progress(conn, run_id, params, "done", 5, 5)
        feedback_store.update_run(
            conn,
            run_id,
            status=status,
            finished_at=_now(conn),
            params_json=params,
        )
        _commit(conn, options)
        return run_id
    except Cancelled as error:
        params["error"] = str(error)
        params.setdefault("progress", {})
        if isinstance(params["progress"], dict):
            params["progress"]["stage"] = "cancelled"
        _save_params(conn, run_id, params)
        feedback_store.update_run(
            conn,
            run_id,
            status="partial",
            raw_output=last_raw or None,
            finished_at=_now(conn),
            params_json=params,
        )
        _commit(conn, options)
        return run_id
    except CostLimitExceeded:
        from feedback_pipeline.run_errors import public_failure

        params = public_failure(params)
        _save_params(conn, run_id, params)
        feedback_store.update_run(
            conn,
            run_id,
            status="partial",
            raw_output=last_raw or None,
            finished_at=_now(conn),
            params_json=params,
        )
        _commit(conn, options)
        return run_id
    except Exception:
        from feedback_pipeline.run_errors import public_failure

        row = conn.execute(
            "SELECT report_json FROM feedback_run WHERE id = ?", (run_id,)
        ).fetchone()
        has_report = bool(row and row[0])
        status = "failed" if not has_report else "partial"
        params = public_failure(params, cards=has_report)
        feedback_store.update_run(
            conn,
            run_id,
            status=status,
            raw_output=last_raw or "",
            finished_at=_now(conn),
            params_json=params,
        )
        _commit(conn, options)
        return run_id
