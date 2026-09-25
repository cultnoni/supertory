"""일반문학 단편 리포트. 교정 카드는 만들지 않는다."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import literary_form
from feedback_pipeline import prompt_loader
from feedback_pipeline.config import (
    FEEDBACK_MODEL,
    LITERATURE_PROMPT_VERSION,
    LITERATURE_STAGE_MODELS,
    MAX_COST_USD_PER_RUN,
)
from feedback_pipeline.literature_cards import card_rows, generate_literature_cards
from feedback_pipeline.literature_compare import snapshot_paragraphs, stabilize_comparison
from feedback_pipeline.literature_signals import collect_signals, spell_error_count
from feedback_pipeline.literature_style import extract_and_apply
from feedback_pipeline.literature_cache import cache_prefix, settings_for_stage
from feedback_pipeline.literature_reuse import STAGE_ORDER
from feedback_pipeline.literature_rubric_complete import (
    ensure_short_rubric,
    missing_keys,
    required_keys_prompt_line,
    required_short_keys,
)
from feedback_pipeline.literature_text import (
    MANUSCRIPT_MARK,
    assemble_manuscript,
    format_numbered_manuscript,
    load_scene_rows,
    locate_quote,
)
from feedback_pipeline.literature_verify import verify_literature_report
from feedback_pipeline.runner import CostLimitExceeded, _add_usage, _check_cost
from feedback_store import add_cards, create_run, set_primary, update_run

PIPELINE = "literature_short"
_VERDICTS = {"works", "room", "problem"}
_RUBRIC_KEYS = (
    "opening",
    "pov",
    "scene_summary",
    "character",
    "motif",
    "implication",
    "ending",
    "economy",
    "style",
    "title",
)
_KEY_ALIASES = {
    "opening": "opening",
    "도입": "opening",
    "pov": "pov",
    "시점": "pov",
    "시점_서술_거리": "pov",
    "시점·서술 거리": "pov",
    "시점 서술 거리": "pov",
    "scene_summary": "scene_summary",
    "장면과_요약": "scene_summary",
    "장면과 요약": "scene_summary",
    "character": "character",
    "인물": "character",
    "motif": "motif",
    "이미지_모티프": "motif",
    "이미지·모티프": "motif",
    "모티프": "motif",
    "implication": "implication",
    "함축": "implication",
    "ending": "ending",
    "결말": "ending",
    "economy": "economy",
    "경제성": "economy",
    "style": "style",
    "문체": "style",
    "문체_일관성": "style",
    "문체 일관성": "style",
    "title": "title",
    "제목": "title",
    "novelty": "novelty",
    "새로움": "novelty",
}
_VERDICT_ALIASES = {
    "works": "works",
    "잘 작동함": "works",
    "잘작동함": "works",
    "room": "room",
    "보완 여지": "room",
    "보완여지": "room",
    "problem": "problem",
    "문제": "problem",
}
_STATUS_ALIASES = {
    "resolved": "resolved",
    "해소": "resolved",
    "partial": "partial",
    "일부": "partial",
    "same": "same",
    "동일": "same",
    "여전": "same",
    "new": "new",
    "새로": "new",
    "신규": "new",
}


def _pick(raw: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in raw and raw[name] not in (None, ""):
            return raw[name]
    return None


def _text(raw: dict[str, Any], *names: str) -> str:
    return str(_pick(raw, *names) or "").strip()


def _canon_key(value: Any) -> str:
    text = str(value or "").strip()
    return _KEY_ALIASES.get(text, text)


def _canon_verdict(value: Any) -> str:
    text = str(value or "").strip()
    return _VERDICT_ALIASES.get(text, text)


def _quote_text(raw: dict[str, Any]) -> str:
    return _text(raw, "quote", "인용", "text")


def _list_of(parsed: dict[str, Any], *names: str) -> list[Any]:
    for name in names:
        value = parsed.get(name)
        if isinstance(value, list):
            return value
    return []


def _now(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0])


def _loads_params(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT params_json FROM feedback_run WHERE id = ?",
        (int(run_id),),
    ).fetchone()
    if row is None or not row["params_json"]:
        return {}
    try:
        data = json.loads(row["params_json"])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_params(conn: sqlite3.Connection, run_id: int, params: dict[str, Any]) -> None:
    update_run(conn, run_id, params_json=params)


def _commit(conn: sqlite3.Connection, options: dict[str, Any]) -> None:
    if options.get("autocommit"):
        conn.commit()


def _progress(conn, run_id, params, stage, done, total) -> None:
    params["progress"] = {"stage": stage, "done": int(done), "total": int(total)}
    _save_params(conn, run_id, params)


def _stage_model(options: dict[str, Any], stage: str) -> str:
    custom = options.get("stage_models") if isinstance(options.get("stage_models"), dict) else {}
    return str(custom.get(stage) or LITERATURE_STAGE_MODELS.get(stage) or FEEDBACK_MODEL)


def _reuse_from(options: dict[str, Any]) -> str:
    reuse = options.get("reuse") if isinstance(options.get("reuse"), dict) else {}
    return str(reuse.get("from") or "")


def _run_stage(options: dict[str, Any], stage: str) -> bool:
    start = _reuse_from(options)
    if start not in STAGE_ORDER:
        return True
    return STAGE_ORDER.index(stage) >= STAGE_ORDER.index(start)


def _invoke(
    claude,
    prompt: str,
    schema: dict[str, Any],
    model: str,
    *,
    max_tokens: int,
    cached_prefix: str = "",
) -> dict[str, Any]:
    from feedback_pipeline.literature_cache import build_cached_system, with_schema_instruction

    common = prompt_loader.load_text("literature/system.txt").rstrip()
    prefix = str(cached_prefix or "").strip()
    system: str | list = build_cached_system(prefix, common) if prefix else common
    return claude.generate(
        with_schema_instruction(prompt, schema),
        model=model,
        system=system,
        thinking="off",
        max_tokens=max_tokens,
        timeout=180.0,
    )


def _timed_usage(params, stage, result, model, started: float) -> None:
    _add_usage(params, stage, result, model)
    stages = (params.get("usage") or {}).get("stages") or []
    if stages and isinstance(result, dict):
        stages[-1]["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        stages[-1]["model"] = model
        if result.get("structured_output"):
            stages[-1]["structured_output"] = result.get("structured_output")
        reject = str(result.get("structured_reject_message") or "").strip()
        if reject:
            stages[-1]["structured_reject"] = reject[:300]


def _project_row(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM project WHERE id = ?", (int(project_id),)).fetchone()
    return dict(row) if row is not None else {}


def _settings_text(conn: sqlite3.Connection, project: dict[str, Any]) -> str:
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
        text = str(project.get(key) or "").strip()
        lines.append(f"{label}: {text or '(비어 있음)'}")
    lines.append("문체 관찰(목표 아님·평가 근거 금지):")
    for key, label in observe_labels:
        text = str(project.get(key) or "").strip()
        lines.append(f"  {label}: {text or '(비어 있음)'}")
    people = conn.execute(
        "SELECT name, short_description, profile_md FROM character "
        "WHERE project_id = ? AND deleted_at IS NULL ORDER BY sort_order, id",
        (int(project.get("id") or 0),),
    ).fetchall()
    if people:
        lines.append("인물:")
        for person in people:
            name = str(person["name"] or "").strip()
            bits = [str(person["short_description"] or "").strip(), str(person["profile_md"] or "").strip()]
            extra = " / ".join(bit for bit in bits if bit)[:800]
            lines.append(f"- {name}" + (f": {extra}" if extra else ""))
    return "\n".join(lines)


def _contest_text(project: dict[str, Any], pages: float) -> str:
    fields = literary_form.contest_fields_from(project)
    if not fields["contest_prep"]:
        return "공모전 준비 중: 꺼짐. 심사위원 관점과 새로움 항목을 쓰지 않는다."
    parts = ["공모전 준비 중: 켜짐."]
    if fields["contest_name"]:
        parts.append(f"목표 공모전: {fields['contest_name']}")
    if fields["contest_pages_min"] is not None or fields["contest_pages_max"] is not None:
        parts.append(
            "분량 규정: "
            f"{fields['contest_pages_min'] if fields['contest_pages_min'] is not None else '없음'}"
            " ~ "
            f"{fields['contest_pages_max'] if fields['contest_pages_max'] is not None else '없음'}매"
        )
    parts.append(f"이번 원고지 약 {pages}매(근사치).")
    return "\n".join(parts)


def _previous_report(conn: sqlite3.Connection, project_id: int, run_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, report_json FROM feedback_run
        WHERE project_id = ? AND pipeline = ? AND id != ?
          AND status IN ('ok', 'partial') AND report_json IS NOT NULL
        ORDER BY id DESC LIMIT 1
        """,
        (int(project_id), PIPELINE, int(run_id)),
    ).fetchone()
    if row is None or not row["report_json"]:
        return None
    try:
        data = json.loads(row["report_json"])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    data["_run_id"] = int(row["id"])
    return data


def _previous_brief(previous: dict[str, Any] | None) -> str:
    if not previous:
        return "(없음)"
    diagnoses = []
    for item in previous.get("diagnoses") or []:
        if not isinstance(item, dict):
            continue
        diagnoses.append(
            {
                "key": item.get("key"),
                "verdict": item.get("verdict"),
                "note": item.get("note"),
            }
        )
    reading = previous.get("reading") if isinstance(previous.get("reading"), dict) else {}
    return json.dumps(
        {"reading": reading, "diagnoses": diagnoses},
        ensure_ascii=False,
    )


def _attach_evidence(assembled: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    quote = _quote_text(raw)
    hint = raw.get("para")
    if hint in (None, ""):
        hint = raw.get("n")
    located = locate_quote(assembled, quote, hint)
    if located is None:
        return {
            "quote": quote,
            "para": raw.get("para"),
            "scene_id": raw.get("scene_id"),
            "local": raw.get("local"),
        }
    return {
        "quote": located["quote"],
        "para": located["para"],
        "scene_id": located["scene_id"],
        "local": located["local"],
    }


def _evidence_list(raw: dict[str, Any], assembled: dict[str, Any]) -> list[dict[str, Any]]:
    rows = None
    for name in ("evidence", "근거", "quotes"):
        value = raw.get(name)
        if isinstance(value, list):
            rows = value
            break
    if rows is None:
        rows = [raw] if _quote_text(raw) else []
    evidence = []
    for item in rows:
        if isinstance(item, dict):
            evidence.append(_attach_evidence(assembled, item))
    return evidence


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "예"}
    return bool(value)


def _diagnosis_from(
    raw: dict[str, Any],
    assembled: dict[str, Any],
    allowed: set[str],
) -> dict[str, Any] | None:
    key = _canon_key(_pick(raw, "key", "항목", "item"))
    if key not in allowed:
        return None
    verdict = _canon_verdict(_pick(raw, "verdict", "판정"))
    if verdict not in _VERDICTS:
        return None
    return {
        "key": key,
        "verdict": verdict,
        "intentional": _as_bool(_pick(raw, "intentional", "의도적")),
        "note": _text(raw, "note", "설명", "판단", "body"),
        "evidence": _evidence_list(raw, assembled),
    }


def _strength_from(raw: dict[str, Any], assembled: dict[str, Any]) -> dict[str, Any] | None:
    located = _attach_evidence(assembled, raw)
    title = _text(raw, "title", "제목")
    body = _text(raw, "body", "본문", "설명")
    if not title and not body and not located.get("quote"):
        return None
    return {"title": title, "body": body, **located}


def _normalize_scene_map(parsed: dict[str, Any]) -> dict[str, Any]:
    scenes = []
    for raw in _list_of(parsed, "scenes", "장면_지도"):
        if not isinstance(raw, dict):
            continue
        characters = _pick(raw, "characters", "등장인물") or []
        if isinstance(characters, str):
            characters = [part.strip() for part in characters.split(",") if part.strip()]
        scenes.append(
            {
                "scene_no": _pick(raw, "scene_no", "장면번호"),
                "para_range": _text(raw, "para_range", "문단범위"),
                "pov": _text(raw, "pov", "시점인물"),
                "when_where": _text(raw, "when_where", "시간_장소"),
                "characters": [str(item) for item in characters if str(item).strip()],
                "summary": _text(raw, "summary", "한줄요약"),
                "narration": _text(raw, "narration", "서술유형"),
            }
        )
    motifs = []
    for raw in _list_of(parsed, "motifs", "반복_이미지_모티프"):
        if not isinstance(raw, dict):
            continue
        locations = _pick(raw, "locations", "등장위치") or []
        if isinstance(locations, str):
            locations = [locations]
        motifs.append(
            {
                "image": _text(raw, "image", "모티프"),
                "locations": [str(item) for item in locations if str(item).strip()],
            }
        )
    return {
        "scenes": scenes,
        "first_sentence": _text(parsed, "first_sentence", "첫문장"),
        "last_sentence": _text(parsed, "last_sentence", "마지막문장"),
        "motifs": motifs,
    }


def _normalize_rubric(parsed: dict[str, Any], assembled: dict[str, Any], contest_on: bool) -> dict[str, Any]:
    allowed = set(_RUBRIC_KEYS)
    if contest_on:
        allowed.add("novelty")
    items = []
    for raw in _list_of(parsed, "items", "항목", "diagnoses", "진단"):
        if not isinstance(raw, dict):
            continue
        item = _diagnosis_from(raw, assembled, allowed)
        if item is not None:
            items.append(item)
    strengths = []
    for raw in _list_of(parsed, "strengths", "강점"):
        if not isinstance(raw, dict):
            continue
        item = _strength_from(raw, assembled)
        if item is not None:
            strengths.append(item)
    return {"items": items, "strengths": strengths}


def _normalize_comparison(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    items = []
    for item in _list_of(raw, "items", "항목"):
        if not isinstance(item, dict):
            continue
        status = _STATUS_ALIASES.get(str(_pick(item, "status", "상태") or "").strip(), "")
        if status not in {"resolved", "partial", "same", "new"}:
            continue
        items.append(
            {
                "key": _canon_key(_pick(item, "key", "항목")),
                "status": status,
                "note": _text(item, "note", "설명"),
            }
        )
    changed = _as_bool(_pick(raw, "reading_changed", "읽기변화"))
    if not items and not changed:
        return None
    return {"reading_changed": changed, "items": items}


def _normalize_report(parsed: dict[str, Any], assembled: dict[str, Any]) -> dict[str, Any]:
    overview = parsed.get("overview") if isinstance(parsed.get("overview"), dict) else {}
    reading = parsed.get("reading") if isinstance(parsed.get("reading"), dict) else {}
    allowed = set(_RUBRIC_KEYS) | {"novelty"}
    diagnoses = []
    for raw in _list_of(parsed, "diagnoses", "항목", "진단"):
        if not isinstance(raw, dict):
            continue
        item = _diagnosis_from(raw, assembled, allowed)
        if item is not None:
            diagnoses.append(item)
    strengths = []
    for raw in _list_of(parsed, "strengths", "강점"):
        if not isinstance(raw, dict):
            continue
        item = _strength_from(raw, assembled)
        if item is not None:
            strengths.append(item)
    tasks = [str(item).strip() for item in _list_of(parsed, "tasks", "과제") if str(item).strip()]
    return {
        "overview": {
            "reader": _text(overview, "reader", "독자"),
            "editor": _text(overview, "editor", "편집자"),
            "critic": _text(overview, "critic", "비평가"),
            "judge": _text(overview, "judge", "심사위원"),
        },
        "reading": {
            "body": _text(reading, "body", "본문"),
            "intent_gap": _text(reading, "intent_gap", "기획의도차이"),
        },
        "strengths": strengths,
        "diagnoses": diagnoses,
        "tasks": tasks[:3],
        "comparison": _normalize_comparison(parsed.get("comparison")),
    }


def render_literature_md(report: dict[str, Any]) -> str:
    overview = report.get("overview") if isinstance(report.get("overview"), dict) else {}
    reading = report.get("reading") if isinstance(report.get("reading"), dict) else {}
    lines = ["AI 검사, 확인 필요", ""]
    lines.append("총평")
    for key, label in (("reader", "독자"), ("editor", "편집자"), ("critic", "비평가"), ("judge", "심사위원")):
        text = str(overview.get(key) or "").strip()
        if text:
            lines.append(f"{label}: {text}")
    lines.append("")
    lines.append("토리가 읽은 이 작품")
    if reading.get("body"):
        lines.append(str(reading.get("body")))
    if reading.get("intent_gap"):
        lines.append(str(reading.get("intent_gap")))
    lines.append("")
    lines.append("강점")
    for item in report.get("strengths") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('title') or ''} {item.get('quote') or ''}".strip())
    lines.append("")
    lines.append("항목별 진단")
    for item in report.get("diagnoses") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('key')} {item.get('verdict')}: {item.get('note') or ''}")
    lines.append("")
    if report.get("card_count") is not None:
        lines.append("")
        lines.append(f"교정 카드 {int(report.get('card_count') or 0)}개")
    lines.append("우선 퇴고 과제")
    for task in report.get("tasks") or []:
        lines.append(f"- {task}")
    comparison = report.get("comparison")
    if isinstance(comparison, dict) and (comparison.get("items") or comparison.get("reading_changed")):
        lines.append("")
        lines.append("이전 버전과 비교")
        lines.append("읽기가 달라졌습니다." if comparison.get("reading_changed") else "읽기는 그대로입니다.")
        for item in comparison.get("items") or []:
            if isinstance(item, dict):
                lines.append(f"- {item.get('key')} {item.get('status')}: {item.get('note') or ''}")
    return "\n".join(lines).strip()


def run_literature_short(
    conn: sqlite3.Connection,
    project_id: int,
    run_id: int,
    options: dict[str, Any] | None = None,
    claude: Any | None = None,
) -> int:
    """이미 만들어진 literature_short 실행을 이어서 돌린다."""
    from feedback_pipeline.claude_client import get_client

    options = dict(options or {})
    caller = claude or get_client()
    project_id = int(project_id)
    run_id = int(run_id)
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
    params.setdefault("progress", {"stage": "start", "done": 0, "total": 5})
    project = _project_row(conn, project_id)
    if literary_form.literary_track(project) != "short":
        raise ValueError("단편 작품이 아닙니다.")
    assembled = assemble_manuscript(load_scene_rows(conn, project_id))
    if not assembled["paragraphs"]:
        raise ValueError("분석할 원고가 없습니다.")
    manuscript = format_numbered_manuscript(assembled)
    # 실행 전체에서 캐시 앞부분을 글자 하나까지 고정한다. 문체 추출 뒤 설정집이
    # 바뀌어도 캐시 키는 그대로 두고, 갱신분은 단계 지시(비캐시)에만 붙인다.
    frozen_settings = _settings_text(conn, project)
    shared_prefix = cache_prefix(manuscript, frozen_settings)
    options["cache_prefix"] = shared_prefix
    options["cache_settings"] = frozen_settings
    contest_on = bool(literary_form.contest_fields_from(project)["contest_prep"])
    contest = _contest_text(project, float(assembled["paper_pages"]))
    cancel_event = options.get("cancel_event")

    def cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("취소되었습니다.")

    cancelled()
    started = time.perf_counter()
    if _run_stage(options, "style"):
        style = extract_and_apply(
            conn,
            project_id,
            mode="fill_empty",
            claude=caller,
            model=_stage_model(options, "style"),
            cached_prefix=shared_prefix,
        )
    else:
        style = {"skipped": True, "usage": None, "model": _stage_model(options, "style")}
    if style.get("skipped"):
        _add_usage(params, "style", {"usage": {"input_tokens": 0, "output_tokens": 0}}, _stage_model(options, "style"))
        stages = params["usage"]["stages"]
        stages[-1]["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        stages[-1]["skipped"] = True
    else:
        _timed_usage(params, "style", style.get("result") or {"usage": style.get("usage")}, style.get("model") or "", started)
    _check_cost(params, max_cost)
    project = _project_row(conn, project_id)
    live_settings = _settings_text(conn, project)
    stage_settings_note = settings_for_stage(frozen_settings, live_settings)
    _progress(conn, run_id, params, "style", 1, 5)
    _commit(conn, options)

    spell = spell_error_count(assembled["plain"])
    signals = collect_signals(
        assembled["paragraphs"],
        spell_error_count=spell,
        contest=literary_form.contest_fields_from(project),
    )
    params["signals_summary"] = signals["summary"]
    _progress(conn, run_id, params, "signals", 2, 5)
    _commit(conn, options)
    cancelled()

    scene_model = _stage_model(options, "scene_map")
    started = time.perf_counter()
    if _run_stage(options, "scene_map"):
        scene_result = _invoke(
            caller,
            prompt_loader.fill_template(
                prompt_loader.load_text("literature/scene_map_prompt.txt"),
                {"manuscript": "원고는 메시지 앞부분에 있다."},
            ),
            prompt_loader.load_json("literature/scene_map_schema.json"),
            scene_model,
            max_tokens=8192,
            cached_prefix=shared_prefix,
        )
        _timed_usage(params, "scene_map", scene_result, scene_model, started)
        _check_cost(params, max_cost)
        scene_parsed = scene_result.get("parsed") if isinstance(scene_result.get("parsed"), dict) else {}
        scene_map = _normalize_scene_map(scene_parsed)
    else:
        stored = options.get("reuse") if isinstance(options.get("reuse"), dict) else {}
        scene_map = stored.get("scene_map") if isinstance(stored.get("scene_map"), dict) else {}
    _progress(conn, run_id, params, "scene_map", 3, 5)
    _commit(conn, options)
    cancelled()

    novelty_rule = (
        "공모전 준비가 켜져 있다. 항목에 novelty(새로움)를 넣고, 참고 의견임을 note에 밝혀라."
        if contest_on
        else "공모전 준비가 꺼져 있다. novelty 항목을 넣지 마라."
    )
    rubric_model = _stage_model(options, "rubric")
    started = time.perf_counter()
    rubric_result: dict[str, Any] = {}
    rubric_log: list[dict[str, str]] = []
    required_line = required_keys_prompt_line(short=True, contest_on=contest_on)
    if _run_stage(options, "rubric"):
        rubric_vars = {
            "required_keys": required_line,
            "novelty_rule": novelty_rule,
            "settings": stage_settings_note,
            "guidance": str(project.get("tory_priority_md") or "").strip() or "(없음)",
            "signals": signals["summary"],
            "scene_map": json.dumps(scene_map, ensure_ascii=False),
            "manuscript": "원고는 메시지 앞부분에 있다.",
        }
        rubric_result = _invoke(
            caller,
            prompt_loader.fill_template(
                prompt_loader.load_text("literature/rubric_prompt.txt"),
                rubric_vars,
            ),
            prompt_loader.load_json("literature/rubric_schema.json"),
            rubric_model,
            max_tokens=8000,
            cached_prefix=shared_prefix,
        )
        _timed_usage(params, "rubric", rubric_result, rubric_model, started)
        _check_cost(params, max_cost)
        rubric_parsed = rubric_result.get("parsed") if isinstance(rubric_result.get("parsed"), dict) else {}
        rubric = _normalize_rubric(rubric_parsed, assembled, contest_on)
        need = required_short_keys(contest_on=contest_on)
        absent = missing_keys(list(rubric.get("items") or []), need)
        if absent:
            retry_started = time.perf_counter()
            rubric_vars["required_keys"] = (
                required_line + f"\n빠진 항목을 반드시 채워라: {', '.join(absent)}"
            )
            retry = _invoke(
                caller,
                prompt_loader.fill_template(
                    prompt_loader.load_text("literature/rubric_prompt.txt"),
                    rubric_vars,
                ),
                prompt_loader.load_json("literature/rubric_schema.json"),
                rubric_model,
                max_tokens=8000,
                cached_prefix=shared_prefix,
            )
            _timed_usage(params, "rubric_retry", retry, rubric_model, retry_started)
            _check_cost(params, max_cost)
            rubric_parsed = retry.get("parsed") if isinstance(retry.get("parsed"), dict) else {}
            rubric = _normalize_rubric(rubric_parsed, assembled, contest_on)
        rubric, _ = ensure_short_rubric(rubric, contest_on=contest_on, log=rubric_log)
    else:
        stored = options.get("reuse") if isinstance(options.get("reuse"), dict) else {}
        rubric = stored.get("rubric") if isinstance(stored.get("rubric"), dict) else {"items": [], "strengths": []}
        rubric_parsed = {}
        rubric, _ = ensure_short_rubric(rubric, contest_on=contest_on, log=rubric_log)
    if _run_stage(options, "rubric") and not rubric["items"]:
        params["rubric_debug"] = {
            "structured_output": rubric_result.get("structured_output"),
            "structured_reject": str(rubric_result.get("structured_reject_message") or "")[:300],
            "sample": json.dumps(rubric_parsed, ensure_ascii=False)[:1500],
        }
    params["rubric_completion_log"] = rubric_log
    _progress(conn, run_id, params, "rubric", 4, 5)
    _commit(conn, options)
    cancelled()

    previous = _previous_report(conn, project_id, run_id)
    comparison_rule = (
        "이전 리포트가 있다. comparison에 이전 진단마다 resolved, partial, same, new 중 하나를 적어라. "
        "토리가 읽은 이 작품이 달라졌으면 reading_changed를 true로 하라."
        if previous
        else "이전 리포트가 없다. comparison.items는 빈 배열, reading_changed는 false로 하라."
    )
    judge_rule = (
        "심사위원을 overview.judge에 짧게 쓴다. 좋은 점 하나, 결정적 약점 하나, "
        "본심에 올린다면 이유와 당선작이 되려면 필요한 것을 말한다. "
        "첫 몇 페이지, 새로움은 참고 의견, 맞춤법 신호 개수로 기본 교정이 필요한 수준인지만, "
        "원고지 매수와 분량 규정을 다룬다. 당선 확률이나 등급은 쓰지 않는다."
        if contest_on
        else "심사위원 관점을 쓰지 않는다. overview.judge는 빈 문자열로 한다."
    )
    report_model = _stage_model(options, "report")
    report_result: dict[str, Any] = {"text": ""}
    if not _run_stage(options, "report"):
        stored = options.get("reuse") if isinstance(options.get("reuse"), dict) else {}
        payload = dict(stored.get("report") or {})
        payload.setdefault("scene_map", scene_map)
        payload.setdefault("rubric", rubric)
    else:
        report_prompt = prompt_loader.fill_template(
            prompt_loader.load_text("literature/report_prompt.txt"),
            {
                "judge_rule": judge_rule,
                "comparison_rule": comparison_rule,
                "settings": stage_settings_note,
                "contest": contest,
                "scene_map": json.dumps(scene_map, ensure_ascii=False),
                "rubric": json.dumps(rubric, ensure_ascii=False),
                "previous": _previous_brief(previous),
            },
        )
        if MANUSCRIPT_MARK in report_prompt:
            raise RuntimeError("리포트 단계에 원고 전문이 들어갔습니다.")
        started = time.perf_counter()
        report_result = _invoke(
            caller,
            report_prompt,
            prompt_loader.load_json("literature/report_schema.json"),
            report_model,
            max_tokens=8000,
            cached_prefix=shared_prefix,
        )
        _timed_usage(params, "report", report_result, report_model, started)
        _check_cost(params, max_cost)
        drafted = _normalize_report(
            report_result.get("parsed") if isinstance(report_result.get("parsed"), dict) else {},
            assembled,
        )
        verified, log = verify_literature_report(
            drafted,
            source_text=assembled["plain"],
            rubric=rubric,
            style_choice=str(project.get("style_choice") or ""),
            contest_on=contest_on,
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
            "scene_map": scene_map,
            "rubric": rubric,
            "signals": {
                "paper_pages": signals["paper_pages"],
                "spell_error_count": signals["spell_error_count"],
                "summary": signals["summary"],
                "signals": signals["signals"],
            },
            "verification_log": list(rubric_log) + list(log),
            **verified,
        }
    update_run(
        conn,
        run_id,
        report_json=payload,
        report_md=render_literature_md(payload),
        raw_output=str(report_result.get("text") or "")[:20000],
        params_json=params,
        model=report_model,
        prompt_version=LITERATURE_PROMPT_VERSION,
    )
    _progress(conn, run_id, params, "cards", 5, 6)
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
        if built:
            add_cards(conn, run_id, card_rows(built))
    except Exception:
        from feedback_pipeline.run_errors import public_failure

        payload["card_count"] = 0
        payload["verification_log"] = list(payload.get("verification_log") or []) + card_log
        params = public_failure(params, cards=True)
        update_run(
            conn, run_id, status="partial", report_json=payload,
            report_md=render_literature_md(payload), params_json=params,
            raw_output="", finished_at=_now(conn),
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
        report_md=render_literature_md(payload),
        planned_cards=len(built),
        finished_at=_now(conn),
        params_json=params,
    )
    scene_ids = conn.execute(
        "SELECT scene_id FROM feedback_run_scene WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    for scene in scene_ids:
        set_primary(conn, run_id, int(scene["scene_id"]))
    _progress(conn, run_id, params, "done", 6, 6)
    _commit(conn, options)
    return run_id


def prepare_literature_run(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    model: str,
    prompt_version: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """실행과 회차 스냅샷을 만들고 식별자를 돌려준다. 카드는 없다."""
    assembled = assemble_manuscript(load_scene_rows(conn, project_id))
    if not assembled["scenes"]:
        raise ValueError("분석할 회차가 없습니다.")
    if not assembled["paragraphs"]:
        raise ValueError("분석할 원고가 없습니다.")
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
        "source_hash": assembled["source_hash"],
        "paragraph_count": len(assembled["paragraphs"]),
        "paper_pages": assembled["paper_pages"],
    }


__all__ = ["PIPELINE", "prepare_literature_run", "run_literature_short", "CostLimitExceeded"]
