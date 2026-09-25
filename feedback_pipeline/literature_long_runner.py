"""일반문학 장편 리포트·카드. 단위(장/회차) 단위로 돈다."""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from typing import Any

import literary_form
from feedback_pipeline import prompt_loader
from feedback_pipeline.config import (
    FEEDBACK_MODEL,
    LITERARY_STYLE_LOOKBACK,
    LITERATURE_LONG_PROMPT_VERSION,
    LITERATURE_LONG_STAGE_MODELS,
    MAX_COST_USD_PER_RUN,
)
from feedback_pipeline.literature_cache import (
    build_cached_system,
    with_schema_instruction,
)
from feedback_pipeline.literature_cards import card_rows, generate_literature_cards
from feedback_pipeline.literature_compare import snapshot_paragraphs, stabilize_comparison
from feedback_pipeline.literature_context import (
    assemble_unit,
    build_prior_context,
    find_unit_for_scene,
    neighbor_scene_texts,
    refresh_prior_summaries,
    unit_label,
    unit_source_hash,
)
from feedback_pipeline.literature_past_facts import soften_report_past_facts
from feedback_pipeline.literature_reuse import STAGE_ORDER
from feedback_pipeline.literature_rubric_complete import (
    ensure_long_rubric,
    missing_keys,
    required_keys_prompt_line,
    required_long_keys,
)
from feedback_pipeline.literature_runner import (
    _attach_evidence,
    _commit,
    _contest_text,
    _diagnosis_from,
    _loads_params,
    _normalize_comparison,
    _normalize_scene_map,
    _progress,
    _project_row,
    _save_params,
    _strength_from,
    _timed_usage,
    render_literature_md,
)
from feedback_pipeline.literature_settings_write import (
    apply_settings_writes,
    list_baits,
    list_characters,
)
from feedback_pipeline.literature_signals import collect_signals, spell_error_count
from feedback_pipeline.literature_style import extract_and_apply
from feedback_pipeline.literature_text import (
    MANUSCRIPT_MARK,
    format_numbered_manuscript,
    load_scene_rows,
)
from feedback_pipeline.literature_units import load_literary_units
from feedback_pipeline.literature_verify import verify_literature_report
from feedback_pipeline.runner import CostLimitExceeded, _add_usage, _check_cost
from feedback_store import add_cards, create_run, set_primary, update_run

PIPELINE = "literature_long"

_INTERNAL_KEYS = {"pov", "scene_summary", "character_interior", "implication", "style", "opening", "ending", "novelty"}
_IN_WORK_KEYS = {
    "continuity",
    "role",
    "character_consistency",
    "character_arc",
    "motif",
    "chapter_edges",
    "opening",
    "ending",
    "novelty",
}
_VERDICT_NONE = "none"


def _stage_model(options: dict[str, Any], stage: str) -> str:
    custom = options.get("stage_models") if isinstance(options.get("stage_models"), dict) else {}
    return str(custom.get(stage) or LITERATURE_LONG_STAGE_MODELS.get(stage) or FEEDBACK_MODEL)


def _reuse_from(options: dict[str, Any]) -> str:
    reuse = options.get("reuse") if isinstance(options.get("reuse"), dict) else {}
    return str(reuse.get("from") or options.get("from") or "style")


def _run_stage(options: dict[str, Any], stage: str) -> bool:
    start = _reuse_from(options)
    if start not in STAGE_ORDER:
        return True
    return STAGE_ORDER.index(stage) >= STAGE_ORDER.index(start)


def _invoke(claude, prompt: str, schema: dict[str, Any], model: str, *, max_tokens: int, cached_system) -> dict[str, Any]:
    return claude.generate(
        with_schema_instruction(prompt, schema),
        model=model,
        system=cached_system,
        thinking="off",
        max_tokens=max_tokens,
        timeout=180.0,
    )


def _now(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()
    return str(row[0])


def _long_settings_text(conn: sqlite3.Connection, project: dict[str, Any]) -> str:
    intent_labels = (
        ("intro_md", "작품소개"),
        ("intent_md", "기획의도"),
        ("tory_priority_md", "작가 지침"),
        ("style_choice", "의도적 선택(작가 뜻)"),
        ("style_habit", "고치고 싶은 습관(작가 뜻)"),
    )
    observe_labels = (
        ("style_narration", "서술 기본값"),
        ("style_sentence", "문장"),
        ("style_dialogue", "대화"),
        ("style_lexicon", "어휘와 이미지"),
    )
    lines = [f"제목: {str(project.get('title') or '').strip() or '(없음)'}"]
    lines.append("(기획의도와의 거리는 작품소개·기획의도만 근거로 쓴다.)")
    for key, label in intent_labels:
        lines.append(f"{label}: {str(project.get(key) or '').strip() or '(비어 있음)'}")
    lines.append("문체 관찰(목표 아님·평가 근거 금지):")
    for key, label in observe_labels:
        lines.append(f"  {label}: {str(project.get(key) or '').strip() or '(비어 있음)'}")
    world = str(project.get("worldbuilding_md") or "").strip()
    lines.append("세계관:\n" + (world or "(비어 있음)"))
    for person in list_characters(conn, int(project.get("id") or 0)):
        aliases = ", ".join(person.get("aliases") or [])
        lines.append(
            f"인물 {person['id']} {person.get('name')}"
            + (f" (별칭: {aliases})" if aliases else "")
            + f": {str(person.get('profile_md') or '')[:600] or '(비어 있음)'}"
        )
    for bait in list_baits(conn, int(project.get("id") or 0)):
        open_mark = " [의도적으로 열어둠]" if bait.get("intentionally_open") else ""
        lines.append(f"모티프·복선 {bait.get('id')}: {bait.get('summary') or ''}{open_mark}")
    terms = [
        str(row["term"])
        for row in conn.execute(
            "SELECT term FROM custom_dictionary_terms WHERE project_id = ? ORDER BY id",
            (int(project.get("id") or 0),),
        ).fetchall()
    ]
    if terms:
        lines.append("토리 사전: " + ", ".join(terms))
    return "\n".join(lines)


def _previous_unit_report(conn, project_id: int, run_id: int, unit: dict[str, Any]) -> dict[str, Any] | None:
    rows = conn.execute(
        """
        SELECT id, report_json, params_json FROM feedback_run
        WHERE project_id = ? AND pipeline = ? AND id != ?
          AND status IN ('ok', 'partial') AND report_json IS NOT NULL
        ORDER BY id DESC
        """,
        (int(project_id), PIPELINE, int(run_id)),
    ).fetchall()
    kind = str(unit.get("kind") or "")
    unit_id = int(unit.get("id") or 0)
    for row in rows:
        try:
            params = json.loads(row["params_json"] or "{}")
        except json.JSONDecodeError:
            params = {}
        stored = params.get("unit") if isinstance(params.get("unit"), dict) else {}
        if str(stored.get("kind") or "") != kind or int(stored.get("id") or 0) != unit_id:
            continue
        try:
            data = json.loads(row["report_json"])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            data["_run_id"] = int(row["id"])
            return data
    return None


def _one_line(text: object, limit: int) -> str:
    line = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(line) <= limit:
        return line
    cut = line[: max(0, limit - 1)].rstrip()
    return cut + "…"


def _normalize_chapter_work(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    out = {}
    for key in ("new_events", "character_changes", "motifs_new", "motifs_returned", "baits_resolved"):
        values = data.get(key) if isinstance(data.get(key), list) else []
        out[key] = [_one_line(item, 60) for item in values if str(item).strip()]
    return out


def _normalize_long_rubric(parsed: dict[str, Any], assembled: dict[str, Any], *, first: bool, last: bool, contest_on: bool) -> dict[str, Any]:
    def take(bucket: str, allowed: set[str]) -> list[dict[str, Any]]:
        items = []
        for raw in parsed.get(bucket) or []:
            if not isinstance(raw, dict):
                continue
            item = _diagnosis_from(raw, assembled, allowed)
            if item is None:
                continue
            if item["key"] == "opening" and not first:
                continue
            if item["key"] == "ending" and not last:
                continue
            if item["key"] == "novelty" and not (contest_on and first):
                continue
            items.append(item)
        return items

    strengths = []
    for raw in parsed.get("strengths") or []:
        if isinstance(raw, dict):
            item = _strength_from(raw, assembled)
            if item is not None:
                strengths.append(item)
    internal = take("internal", _INTERNAL_KEYS)
    in_work = take("in_work", _IN_WORK_KEYS)
    return {
        "items": internal + in_work,
        "internal": internal,
        "in_work": in_work,
        "strengths": strengths,
    }


def _normalize_long_report(parsed: dict[str, Any], assembled: dict[str, Any]) -> dict[str, Any]:
    overview = parsed.get("overview") if isinstance(parsed.get("overview"), dict) else {}
    reading = parsed.get("reading") if isinstance(parsed.get("reading"), dict) else {}
    relation = parsed.get("settings_relation") if isinstance(parsed.get("settings_relation"), dict) else {}

    quote_bank: dict[str, dict[str, Any]] = {}
    for raw in parsed.get("quotes") or []:
        if not isinstance(raw, dict):
            continue
        qid = str(raw.get("id") or "").strip()
        if not qid:
            continue
        attached = _attach_evidence(
            assembled,
            {"quote": raw.get("quote"), "scene_no": raw.get("scene_no")},
        )
        quote_bank[qid] = attached

    def evidence_from(raw: dict[str, Any]) -> list[dict[str, Any]]:
        ids = raw.get("quote_ids") if isinstance(raw.get("quote_ids"), list) else None
        if ids is None and raw.get("quote_id"):
            ids = [raw.get("quote_id")]
        if ids:
            rows = []
            for item in ids:
                qid = str(item or "").strip()
                if qid and qid in quote_bank:
                    rows.append(dict(quote_bank[qid]))
            if rows:
                return rows
        return []

    def diagnoses(field: str) -> list[dict[str, Any]]:
        items = []
        for raw in parsed.get(field) or []:
            if not isinstance(raw, dict):
                continue
            allowed = _INTERNAL_KEYS | _IN_WORK_KEYS | {str(raw.get("key") or "")}
            expanded = dict(raw)
            ev = evidence_from(raw)
            if ev:
                expanded["evidence"] = ev
                expanded["quote"] = ev[0].get("quote")
            item = _diagnosis_from(expanded, assembled, allowed)
            if item is not None:
                items.append(item)
        return items

    def conflict_quote(raw: dict[str, Any]) -> str:
        qid = str(raw.get("quote_id") or "").strip()
        if qid and qid in quote_bank:
            return str(quote_bank[qid].get("quote") or "")
        return str(raw.get("quote") or "").strip()

    conflicts = []
    for raw in (relation.get("conflicts") or parsed.get("conflicts") or []):
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("conflict_id") or "").strip() or f"conflict-{uuid.uuid4().hex[:8]}"
        conflicts.append(
            {
                "conflict_id": cid,
                "title": str(raw.get("title") or "설정집과 어긋납니다").strip(),
                "note": str(raw.get("note") or "").strip(),
                "quote": conflict_quote(raw),
                "settings_section": str(raw.get("settings_section") or "characters").strip(),
                "field": str(raw.get("field") or "").strip(),
                "character_id": int(raw.get("character_id") or 0),
                "bait_id": str(raw.get("bait_id") or ""),
                "draft": str(raw.get("draft") or "").strip(),
                "scene_id": int(raw.get("scene_id") or 0),
            }
        )
    discoveries = [dict(item) for item in (parsed.get("discoveries") or []) if isinstance(item, dict)]
    strengths = []
    for raw in parsed.get("strengths") or []:
        if not isinstance(raw, dict):
            continue
        expanded = dict(raw)
        qid = str(raw.get("quote_id") or "").strip()
        if qid and qid in quote_bank:
            expanded.update(quote_bank[qid])
        item = _strength_from(expanded, assembled)
        if item is not None:
            strengths.append(item)
    tasks = [str(item).strip() for item in (parsed.get("tasks") or []) if str(item).strip()][:3]
    internal = diagnoses("diagnoses_internal")
    in_work = diagnoses("diagnoses_in_work")
    return {
        "overview": {
            "reader": str(overview.get("reader") or "").strip(),
            "editor": str(overview.get("editor") or "").strip(),
            "critic": str(overview.get("critic") or "").strip(),
            "judge": str(overview.get("judge") or "").strip(),
        },
        "reading": {
            "body": str(reading.get("body") or "").strip(),
            "intent_gap": str(reading.get("intent_gap") or "").strip(),
        },
        "strengths": strengths,
        "diagnoses": internal + in_work,
        "diagnoses_internal": internal,
        "diagnoses_in_work": in_work,
        "settings_relation": {
            "conflicts": conflicts,
            "added": [str(item).strip() for item in (relation.get("added") or []) if str(item).strip()],
        },
        "tasks": tasks,
        "comparison": _normalize_comparison(parsed.get("comparison")),
        "discoveries": discoveries,
        "conflicts": conflicts,
        "quotes": list(quote_bank.values()),
    }


def render_literature_long_md(report: dict[str, Any]) -> str:
    base = render_literature_md({**report, "diagnoses": report.get("diagnoses") or []})
    lines = [base, "", "장 내부 진단"]
    for item in report.get("diagnoses_internal") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('key')} {item.get('verdict')}: {item.get('note') or ''}")
    lines.append("")
    lines.append("작품 속 이 장 진단")
    for item in report.get("diagnoses_in_work") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('key')} {item.get('verdict')}: {item.get('note') or ''}")
    relation = report.get("settings_relation") if isinstance(report.get("settings_relation"), dict) else {}
    lines.append("")
    lines.append("설정집과의 관계")
    for item in relation.get("conflicts") or []:
        if isinstance(item, dict):
            lines.append(f"- 모순 {item.get('conflict_id')}: {item.get('note') or ''}")
    for item in relation.get("added") or []:
        lines.append(f"- 추가: {item}")
    return "\n".join(lines).strip()


def conflict_cards(conflicts: list[dict[str, Any]], assembled: dict[str, Any]) -> list[dict[str, Any]]:
    """같은 모순 목록으로 지적형·상 카드를 만든다."""
    cards = []
    paras = assembled.get("paragraphs") or []
    first_scene = int(paras[0]["scene_id"]) if paras else 0
    for raw in conflicts:
        quote = str(raw.get("quote") or "").strip()
        scene_id = int(raw.get("scene_id") or 0) or first_scene
        attached = _attach_evidence(assembled, {"quote": quote, "scene_no": 1})
        n = int(attached.get("n") or attached.get("global_para") or 0)
        local = int(attached.get("local") or 0)
        cards.append(
            {
                "form": "note",
                "tags": ["settings_conflict"],
                "priority": "high",
                "quote": quote or attached.get("quote") or "",
                "original_text": quote or attached.get("quote") or "",
                "reason": str(raw.get("note") or raw.get("title") or "설정집과 어긋납니다"),
                "reader_problem": str(raw.get("title") or "설정집과 원고가 다릅니다"),
                "suggestion": "",
                "scene_id": int(attached.get("scene_id") or scene_id or first_scene),
                "global_para": n,
                "start_para": n or None,
                "end_para": n or None,
                "local_para": local,
                "start_quote": quote[:80] if quote else "",
                "end_quote": "",
                "source_key": "settings_conflict:" + str(raw.get("conflict_id") or ""),
                "intentional": False,
                "conflict_id": str(raw.get("conflict_id") or ""),
                "notification_id": raw.get("notification_id"),
            }
        )
    return cards


def _default_summarizer(scene_id: int, body: dict[str, Any]) -> dict[str, Any]:
    text = str((body or {}).get("content_md") or "")
    plain = " ".join(text.replace("<", " <").split())
    return {
        "summary": json.dumps(
            {
                "events": plain[:400],
                "characters": [],
                "world_facts": [],
                "baits": [],
            },
            ensure_ascii=False,
        )
    }


def prepare_literature_long_run(
    conn: sqlite3.Connection,
    project_id: int,
    scene_id: int,
    *,
    model: str,
    prompt_version: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    units = load_literary_units(conn, project_id)
    unit = find_unit_for_scene(units, int(scene_id))
    if unit is None:
        raise ValueError("지금 연 회차가 속한 분석 단위를 찾지 못했습니다.")
    assembled = assemble_unit(conn, project_id, list(unit.get("scene_ids") or []))
    if not assembled["paragraphs"]:
        raise ValueError("분석할 원고가 없습니다.")
    params = dict(params or {})
    params["unit"] = {
        "kind": unit["kind"],
        "id": unit["id"],
        "ord": unit["ord"],
        "title": unit.get("title") or "",
        "scene_ids": list(unit.get("scene_ids") or []),
        "label": unit_label(unit),
    }
    params["unit_source_hash"] = unit_source_hash(assembled)
    run_id = create_run(
        conn,
        project_id,
        "analyze",
        model,
        prompt_version,
        params,
        pipeline=PIPELINE,
    )
    from feedback_store import add_run_scene

    for scene in assembled["scenes"]:
        add_run_scene(
            conn,
            run_id,
            project_id,
            int(scene["scene_id"]),
            int(scene["ord"]),
            str(scene["title"] or ""),
            None if scene.get("revision_no") is None else int(scene["revision_no"]),
            str(scene["source_hash"] or ""),
            scene["paragraphs"],
            para_offset=int(scene["para_offset"]),
        )
    return {
        "run_id": run_id,
        "unit": params["unit"],
        "source_hash": assembled["source_hash"],
        "paragraph_count": len(assembled["paragraphs"]),
        "paper_pages": assembled["paper_pages"],
    }


def run_literature_long(
    conn: sqlite3.Connection,
    project_id: int,
    run_id: int,
    options: dict[str, Any] | None = None,
    *,
    claude=None,
) -> int:
    from feedback_pipeline.claude_client import LiveClaude

    options = dict(options or {})
    caller = claude or LiveClaude()
    max_cost = float(options.get("max_cost") or MAX_COST_USD_PER_RUN)
    params = _loads_params(conn, run_id)
    params.setdefault(
        "usage",
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cost_input_usd": 0.0,
            "cost_cache_write_usd": 0.0,
            "cost_cache_read_usd": 0.0,
            "cost_output_usd": 0.0,
            "cost_usd": 0.0,
            "stages": [],
        },
    )
    params.setdefault("progress", {"stage": "start", "done": 0, "total": 8})
    project = _project_row(conn, project_id)
    if literary_form.literary_track(project) != "long":
        raise ValueError("장편 작품이 아닙니다.")
    units = load_literary_units(conn, project_id)
    unit_meta = params.get("unit") if isinstance(params.get("unit"), dict) else {}
    unit = find_unit_for_scene(units, int((unit_meta.get("scene_ids") or [0])[0])) or next(
        (item for item in units if int(item.get("id") or 0) == int(unit_meta.get("id") or 0)
         and str(item.get("kind") or "") == str(unit_meta.get("kind") or "")),
        None,
    )
    if unit is None:
        raise ValueError("분석 단위를 찾지 못했습니다.")
    assembled = assemble_unit(conn, project_id, list(unit.get("scene_ids") or []))
    if not assembled["paragraphs"]:
        raise ValueError("분석할 원고가 없습니다.")
    manuscript = format_numbered_manuscript(assembled)
    unit_ord = int(unit.get("ord") or 0)
    first_unit = unit_ord == 0
    finale_kind = str(project.get("literary_finale_kind") or "")
    finale_id = int(project.get("literary_finale_id") or 0)
    last_unit = bool(finale_kind) and finale_kind == str(unit.get("kind") or "") and finale_id == int(unit.get("id") or 0)
    contest_on = bool(literary_form.contest_fields_from(project)["contest_prep"])
    contest = _contest_text(project, float(assembled["paper_pages"]))
    cancel_event = options.get("cancel_event")

    def cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("취소되었습니다.")

    cancelled()
    # 1) 앞 단위 요약 대기열
    summarizer = options.get("summarize_scene_for_index") or _default_summarizer

    def on_summary_progress(done: int, total: int) -> None:
        params["progress"] = {
            "stage": "prior_summaries",
            "done": int(done),
            "total": max(1, int(total) or 1),
            "label": f"앞 장 요약을 준비하는 중 ({done}/{total})",
        }
        _save_params(conn, run_id, params)
        _commit(conn, options)

    refresh_prior_summaries(
        conn,
        project_id,
        units,
        unit_ord,
        summarizer,
        on_progress=on_summary_progress,
    )
    _progress(conn, run_id, params, "prior_summaries", 1, 8)
    _commit(conn, options)
    cancelled()

    def on_usage(stage: str, result: dict[str, Any], model: str) -> None:
        _add_usage(params, stage, result, model)

    prior = build_prior_context(
        conn,
        project_id,
        units,
        unit_ord,
        claude=caller,
        stage_models=options.get("stage_models") if isinstance(options.get("stage_models"), dict) else None,
        on_usage=on_usage,
    )
    _check_cost(params, max_cost)
    neighbors = neighbor_scene_texts(conn, project_id, units, unit_ord)
    frozen_settings = _long_settings_text(conn, project)
    cache_body = (
        f"분석 단위: {unit_label(unit)}\n\n"
        f"원고:\n{manuscript}\n\n"
        f"직전 단위 마지막 장면:\n{neighbors.get('previous_last_scene') or '(없음)'}\n\n"
        f"다음 단위 첫 장면:\n{neighbors.get('next_first_scene') or '(없음)'}\n\n"
        f"이전 단위 요약:\n{prior.get('text') or '(없음)'}\n\n"
        f"설정집:\n{frozen_settings}\n\n"
        f"공모전:\n{contest}"
    )
    shared_prefix = cache_body
    options["cache_prefix"] = shared_prefix
    common = prompt_loader.load_text("literature/system.txt").rstrip()
    cached_system = build_cached_system(shared_prefix, common)

    # style from lookback units if empty
    started = time.perf_counter()
    if _run_stage(options, "style"):
        look_ids: list[int] = []
        for item in units[max(0, unit_ord - LITERARY_STYLE_LOOKBACK + 1) : unit_ord + 1]:
            look_ids.extend(int(sid) for sid in (item.get("scene_ids") or []))
        look_ids = look_ids[: max(1, len(look_ids))]
        look_assembled = assemble_unit(conn, project_id, look_ids) if look_ids else assembled
        style = extract_and_apply(
            conn,
            project_id,
            mode="fill_empty",
            claude=caller,
            model=_stage_model(options, "style"),
            cached_prefix=shared_prefix,
            assembled=look_assembled,
        )
    else:
        style = {"skipped": True, "usage": None, "model": _stage_model(options, "style")}
    if style.get("skipped"):
        _add_usage(params, "style", {"usage": {"input_tokens": 0, "output_tokens": 0}}, _stage_model(options, "style"))
        params["usage"]["stages"][-1]["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        params["usage"]["stages"][-1]["skipped"] = True
    else:
        _timed_usage(params, "style", style.get("result") or {"usage": style.get("usage")}, style.get("model") or "", started)
    _check_cost(params, max_cost)
    project = _project_row(conn, project_id)
    _progress(conn, run_id, params, "style", 2, 8)
    _commit(conn, options)
    cancelled()

    spell = spell_error_count(assembled["plain"])
    signals = collect_signals(
        assembled["paragraphs"],
        spell_error_count=spell,
        contest=literary_form.contest_fields_from(project),
    )
    params["signals_summary"] = signals["summary"]
    _progress(conn, run_id, params, "signals", 3, 8)
    _commit(conn, options)
    cancelled()

    scene_model = _stage_model(options, "scene_map")
    started = time.perf_counter()
    scene_map: dict[str, Any] = {}
    chapter_work: dict[str, Any] = {}
    if _run_stage(options, "scene_map"):
        scene_result = _invoke(
            caller,
            prompt_loader.fill_template(
                prompt_loader.load_text("literature/long_scene_map_prompt.txt"),
                {"manuscript": "원고·설정집·이전 요약은 메시지 앞부분에 있다."},
            ),
            prompt_loader.load_json("literature/long_scene_map_schema.json"),
            scene_model,
            max_tokens=2800,
            cached_system=cached_system,
        )
        _timed_usage(params, "scene_map", scene_result, scene_model, started)
        _check_cost(params, max_cost)
        parsed = scene_result.get("parsed") if isinstance(scene_result.get("parsed"), dict) else {}
        scene_map = _normalize_scene_map(parsed)
        for scene in scene_map.get("scenes") or []:
            if isinstance(scene, dict) and scene.get("summary"):
                scene["summary"] = _one_line(scene.get("summary"), 60)
        chapter_work = _normalize_chapter_work(parsed.get("chapter_work"))
    else:
        stored = options.get("reuse") if isinstance(options.get("reuse"), dict) else {}
        scene_map = stored.get("scene_map") if isinstance(stored.get("scene_map"), dict) else {}
        chapter_work = stored.get("chapter_work") if isinstance(stored.get("chapter_work"), dict) else {}
    _progress(conn, run_id, params, "scene_map", 4, 8)
    _commit(conn, options)
    cancelled()

    extra_rules = []
    if first_unit:
        extra_rules.append("첫 단위이므로 도입(opening) 항목을 넣는다.")
    else:
        extra_rules.append("첫 단위가 아니므로 opening 항목을 넣지 않는다.")
    if last_unit:
        extra_rules.append("마지막 장으로 표시되어 있으므로 결말(ending) 항목을 넣는다.")
    else:
        extra_rules.append("마지막 장이 아니므로 ending 항목을 넣지 않는다.")
    if contest_on and first_unit:
        extra_rules.append("공모전이 켜져 있으므로 첫 단위에 novelty와 심사 관점을 참고로 넣는다.")
    else:
        extra_rules.append("novelty 항목을 넣지 않는다.")

    rubric_model = _stage_model(options, "rubric")
    started = time.perf_counter()
    rubric = {"items": [], "internal": [], "in_work": [], "strengths": []}
    rubric_log: list[dict[str, str]] = []
    if _run_stage(options, "rubric"):
        required_line = required_keys_prompt_line(
            short=False, first=first_unit, last=last_unit, contest_on=contest_on and first_unit
        )
        rubric_vars = {
            "required_keys": required_line,
            "extra_rules": "\n".join(extra_rules),
            "settings": "설정집·이전 요약은 메시지 앞부분에 있다.",
            "guidance": str(project.get("tory_priority_md") or "").strip() or "(없음)",
            "signals": signals["summary"],
            "scene_map": json.dumps({"scene_map": scene_map, "chapter_work": chapter_work}, ensure_ascii=False),
            "manuscript": "원고는 메시지 앞부분에 있다.",
        }
        rubric_result = _invoke(
            caller,
            prompt_loader.fill_template(
                prompt_loader.load_text("literature/long_rubric_prompt.txt"),
                rubric_vars,
            ),
            prompt_loader.load_json("literature/long_rubric_schema.json"),
            rubric_model,
            max_tokens=3200,
            cached_system=cached_system,
        )
        _timed_usage(params, "rubric", rubric_result, rubric_model, started)
        _check_cost(params, max_cost)
        rubric = _normalize_long_rubric(
            rubric_result.get("parsed") if isinstance(rubric_result.get("parsed"), dict) else {},
            assembled,
            first=first_unit,
            last=last_unit,
            contest_on=contest_on,
        )
        need = required_long_keys(
            first=first_unit, last=last_unit, contest_on=contest_on and first_unit
        )
        absent = missing_keys(list(rubric.get("internal") or []), need["internal"]) + missing_keys(
            list(rubric.get("in_work") or []), need["in_work"]
        )
        if absent:
            retry_started = time.perf_counter()
            rubric_vars["required_keys"] = (
                required_line + f"\n빠진 항목을 반드시 채워라: {', '.join(absent)}"
            )
            retry = _invoke(
                caller,
                prompt_loader.fill_template(
                    prompt_loader.load_text("literature/long_rubric_prompt.txt"),
                    rubric_vars,
                ),
                prompt_loader.load_json("literature/long_rubric_schema.json"),
                rubric_model,
                max_tokens=3200,
                cached_system=cached_system,
            )
            _timed_usage(params, "rubric_retry", retry, rubric_model, retry_started)
            _check_cost(params, max_cost)
            rubric = _normalize_long_rubric(
                retry.get("parsed") if isinstance(retry.get("parsed"), dict) else {},
                assembled,
                first=first_unit,
                last=last_unit,
                contest_on=contest_on,
            )
        rubric, _still = ensure_long_rubric(
            rubric,
            first=first_unit,
            last=last_unit,
            contest_on=contest_on and first_unit,
            log=rubric_log,
        )
    else:
        stored = options.get("reuse") if isinstance(options.get("reuse"), dict) else {}
        rubric = stored.get("rubric") if isinstance(stored.get("rubric"), dict) else rubric
        rubric, _ = ensure_long_rubric(
            rubric,
            first=first_unit,
            last=last_unit,
            contest_on=contest_on and first_unit,
            log=rubric_log,
        )
    _progress(conn, run_id, params, "rubric", 5, 8)
    _commit(conn, options)
    cancelled()

    previous = _previous_unit_report(conn, project_id, run_id, unit)
    comparison_rule = (
        "이전 리포트가 있다. comparison에 이전 진단마다 resolved, partial, same, new 중 하나를 적어라."
        if previous
        else "이전 리포트가 없다. comparison.items는 빈 배열, reading_changed는 false."
    )
    judge_rule = (
        "심사위원을 overview.judge에 짧게 쓴다. 당선 확률은 쓰지 않는다."
        if contest_on and first_unit
        else "overview.judge는 빈 문자열."
    )
    report_model = _stage_model(options, "report")
    report_result: dict[str, Any] = {"text": ""}
    if not _run_stage(options, "report"):
        stored = options.get("reuse") if isinstance(options.get("reuse"), dict) else {}
        payload = dict(stored.get("report") or {})
        payload.setdefault("scene_map", scene_map)
        payload.setdefault("chapter_work", chapter_work)
        payload.setdefault("rubric", rubric)
        payload.setdefault("pipeline", PIPELINE)
        payload.setdefault("unit", params.get("unit"))
        conflicts = list(payload.get("conflicts") or [])
    else:
        started = time.perf_counter()
        report_result = _invoke(
            caller,
            prompt_loader.fill_template(
                prompt_loader.load_text("literature/long_report_prompt.txt"),
                {
                    "judge_rule": judge_rule,
                    "comparison_rule": comparison_rule,
                    "settings": "설정집·이전 요약은 메시지 앞부분에 있다.",
                    "contest": contest,
                    "scene_map": json.dumps({"scene_map": scene_map, "chapter_work": chapter_work}, ensure_ascii=False),
                    "rubric": json.dumps(rubric, ensure_ascii=False),
                    "previous": json.dumps(
                        {
                            "reading": (previous or {}).get("reading"),
                            "diagnoses": (previous or {}).get("diagnoses"),
                        },
                        ensure_ascii=False,
                    )
                    if previous
                    else "(없음)",
                },
            ),
            prompt_loader.load_json("literature/long_report_schema.json"),
            report_model,
            max_tokens=3600,
            cached_system=cached_system,
        )
        if MANUSCRIPT_MARK in str(report_result.get("text") or ""):
            raise RuntimeError("리포트 단계에 원고 전문 표식이 남았습니다.")
        _timed_usage(params, "report", report_result, report_model, started)
        _check_cost(params, max_cost)
        drafted = _normalize_long_report(
            report_result.get("parsed") if isinstance(report_result.get("parsed"), dict) else {},
            assembled,
        )
        drafted, past_log = soften_report_past_facts(
            drafted,
            prior_context=str(prior.get("text") or ""),
            settings_text=frozen_settings,
            neighbor_text=(
                f"직전 단위 마지막 장면:\n{neighbors.get('previous_last_scene') or ''}\n"
                f"다음 단위 첫 장면:\n{neighbors.get('next_first_scene') or ''}"
            ),
            current_unit_no=int(unit.get("ord") or 0) + 1,
        )
        verified, log = verify_literature_report(
            {
                "overview": drafted["overview"],
                "reading": drafted["reading"],
                "strengths": drafted["strengths"],
                "diagnoses": drafted["diagnoses"],
                "tasks": drafted["tasks"],
                "comparison": drafted.get("comparison"),
            },
            source_text=assembled["plain"],
            rubric={"items": rubric.get("items") or []},
            style_choice=str(project.get("style_choice") or ""),
            contest_on=contest_on and first_unit,
        )
        if previous and previous.get("_run_id"):
            prev_scenes = []
            for scene_row in conn.execute(
                "SELECT * FROM feedback_run_scene WHERE run_id = ? ORDER BY ord, id",
                (int(previous["_run_id"]),),
            ).fetchall():
                scene = dict(scene_row)
                raw_paragraphs = scene.get("paragraphs_json") or "[]"
                scene["paragraphs"] = json.loads(raw_paragraphs) if isinstance(raw_paragraphs, str) else raw_paragraphs
                prev_scenes.append(scene)
            verified, compare_log = stabilize_comparison(
                verified,
                previous,
                snapshot_paragraphs(prev_scenes),
                assembled["paragraphs"],
            )
            log.extend(compare_log)
        payload = {
            "pipeline": PIPELINE,
            "paper_pages": assembled["paper_pages"],
            "unit": params.get("unit"),
            "unit_index": [
                {
                    "kind": item.get("kind"),
                    "id": item.get("id"),
                    "ord": item.get("ord"),
                    "title": item.get("title"),
                    "scene_ids": list(item.get("scene_ids") or []),
                    "label": unit_label(item),
                }
                for item in units
            ],
            "scene_map": scene_map,
            "chapter_work": chapter_work,
            "rubric": rubric,
            "signals": {
                "paper_pages": signals["paper_pages"],
                "spell_error_count": signals["spell_error_count"],
                "summary": signals["summary"],
            },
            "verification_log": list(rubric_log) + list(past_log) + list(log),
            "diagnoses_internal": drafted.get("diagnoses_internal") or [],
            "diagnoses_in_work": drafted.get("diagnoses_in_work") or [],
            "settings_relation": drafted.get("settings_relation") or {"conflicts": [], "added": []},
            "discoveries": drafted.get("discoveries") or [],
            "conflicts": drafted.get("conflicts") or [],
            **verified,
        }
        # keep split diagnoses if verify flattened
        if not payload.get("diagnoses_internal") and payload.get("diagnoses"):
            payload["diagnoses_internal"] = [
                item for item in payload["diagnoses"] if str(item.get("key") or "") in _INTERNAL_KEYS
            ]
            payload["diagnoses_in_work"] = [
                item for item in payload["diagnoses"] if str(item.get("key") or "") in _IN_WORK_KEYS
            ]
        conflicts = list(payload.get("conflicts") or [])

    payload.setdefault(
        "unit_index",
        [
            {
                "kind": item.get("kind"),
                "id": item.get("id"),
                "ord": item.get("ord"),
                "title": item.get("title"),
                "scene_ids": list(item.get("scene_ids") or []),
                "label": unit_label(item),
            }
            for item in units
        ],
    )

    # settings write from the same conflict/discovery list
    try:
        write_stats = apply_settings_writes(
            conn,
            project_id,
            unit=unit,
            scene_id=int((unit.get("scene_ids") or [0])[0]),
            discoveries=list(payload.get("discoveries") or []),
            conflicts=conflicts,
        )
    except Exception as error:  # noqa: BLE001
        write_stats = {
            "filled": [],
            "pending": [],
            "created": [],
            "questions": [],
            "conflicts": [],
            "active_keys": [],
            "closed_notifications": 0,
            "error": str(error)[:300],
        }
    payload["settings_write"] = {
        "filled": write_stats.get("filled") or [],
        "pending": write_stats.get("pending") or [],
        "created": write_stats.get("created") or [],
        "closed_notifications": write_stats.get("closed_notifications") or 0,
    }
    write_log = list(write_stats.get("verification_log") or [])
    if write_log:
        payload["verification_log"] = list(payload.get("verification_log") or []) + write_log
    # link notification ids onto conflicts
    notif_by_conflict = {
        str(item.get("conflict_id") or ""): item.get("notification")
        for item in (write_stats.get("conflicts") or [])
    }
    for item in conflicts:
        notif = notif_by_conflict.get(str(item.get("conflict_id") or ""))
        if isinstance(notif, dict):
            nested = notif.get("notification") if isinstance(notif.get("notification"), dict) else notif
            item["notification_id"] = nested.get("id") if isinstance(nested, dict) else None
    payload["conflicts"] = conflicts
    payload["settings_relation"] = {
        **(payload.get("settings_relation") or {}),
        "conflicts": conflicts,
        "added": list((payload.get("settings_relation") or {}).get("added") or [])
        + [str(item) for item in (write_stats.get("created") or [])],
    }

    update_run(
        conn,
        run_id,
        report_json=payload,
        report_md=render_literature_long_md(payload),
        raw_output=str(report_result.get("text") or "")[:20000],
        params_json=params,
        model=report_model,
        prompt_version=LITERATURE_LONG_PROMPT_VERSION,
    )
    _progress(conn, run_id, params, "cards", 6, 8)
    _commit(conn, options)

    card_log: list[dict[str, str]] = []
    try:
        built, card_log = generate_literature_cards(
            conn,
            project=project,
            assembled=assembled,
            report=payload,
            signals_summary=str(signals.get("summary") or ""),
            options={**options, "run_id": int(run_id), "pipeline": PIPELINE},
            claude=caller,
            params=params,
            max_cost=max_cost,
        )
        # append contradiction cards from the same conflict list
        extra = conflict_cards(conflicts, assembled)
        for card in extra:
            notif = notif_by_conflict.get(str(card.get("conflict_id") or ""))
            if isinstance(notif, dict):
                nested = notif.get("notification") if isinstance(notif.get("notification"), dict) else notif
                card["notification_id"] = nested.get("id") if isinstance(nested, dict) else None
            built.append(card)
            card_log.append(
                {
                    "action": "settings_conflict_card",
                    "target": str(card.get("conflict_id") or ""),
                    "detail": str(card.get("reason") or "")[:200],
                }
            )
        if built:
            add_cards(conn, run_id, card_rows(built))
    except Exception:
        from feedback_pipeline.run_errors import public_failure

        payload["card_count"] = 0
        payload["verification_log"] = list(payload.get("verification_log") or []) + card_log
        params = public_failure(params, cards=True)
        update_run(
            conn,
            run_id,
            status="partial",
            report_json=payload,
            report_md=render_literature_long_md(payload),
            params_json=params,
            raw_output="",
            finished_at=_now(conn),
        )
        _commit(conn, options)
        return run_id

    payload["verification_log"] = list(payload.get("verification_log") or []) + card_log
    payload["card_count"] = len(built)
    update_run(
        conn,
        run_id,
        status="ok",
        report_json=payload,
        report_md=render_literature_long_md(payload),
        planned_cards=len(built),
        finished_at=_now(conn),
        params_json=params,
    )
    for scene in conn.execute(
        "SELECT scene_id FROM feedback_run_scene WHERE run_id = ?",
        (run_id,),
    ).fetchall():
        set_primary(conn, run_id, int(scene["scene_id"]))
    _progress(conn, run_id, params, "done", 8, 8)
    _commit(conn, options)
    try:
        from feedback_pipeline.literature_midcheck import maybe_create_midcheck_nudge

        unit_count = len(load_literary_units(conn, project_id))
        maybe_create_midcheck_nudge(conn, project_id, unit_count)
        _commit(conn, options)
    except Exception:  # noqa: BLE001
        pass
    return run_id


__all__ = [
    "PIPELINE",
    "prepare_literature_long_run",
    "run_literature_long",
    "CostLimitExceeded",
    "conflict_cards",
    "render_literature_long_md",
]
