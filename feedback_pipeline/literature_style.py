"""문체 1~4 추출. 찬 칸은 파이프라인이 고치지 않는다."""

from __future__ import annotations

import sqlite3
from typing import Any

import literary_form
from feedback_pipeline import prompt_loader
from feedback_pipeline.config import LITERATURE_STAGE_MODELS
from feedback_pipeline.literature_text import assemble_manuscript, format_numbered_manuscript, load_scene_rows

TORI_MARK = "〔토리〕"
EXTRACT_FIELDS = (
    "style_narration",
    "style_sentence",
    "style_dialogue",
    "style_lexicon",
)


def field_state(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "empty"
    if text.startswith(TORI_MARK):
        return "tori"
    return "author"


def with_tori_prefix(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(TORI_MARK):
        return text
    return f"{TORI_MARK} {text}"


def apply_style_drafts(
    current: dict[str, str],
    extracted: dict[str, Any],
    *,
    mode: str,
) -> dict[str, str]:
    """바꿀 칸만 돌려준다.

    fill_empty: 빈 칸만. 작가 글과 기존 〔토리〕 글은 유지.
    button: 작가가 요청한 경우. 빈 칸과 기존 〔토리〕 초안만 다시 쓴다.
    5~6번과 작가 글은 어느 모드에서도 바꾸지 않는다.
    """
    replace_tori = mode == "button"
    updates: dict[str, str] = {}
    for key in EXTRACT_FIELDS:
        state = field_state(current.get(key))
        if state == "author":
            continue
        if state == "tori" and not replace_tori:
            continue
        draft = with_tori_prefix(str((extracted or {}).get(key) or ""))
        if not draft:
            continue
        if draft == str(current.get(key) or "").strip():
            continue
        updates[key] = draft
    return updates


def load_style_values(conn: sqlite3.Connection, project_id: int) -> dict[str, str]:
    columns = ", ".join(literary_form.STYLE_FIELDS)
    row = conn.execute(
        f"SELECT {columns} FROM project WHERE id = ?",
        (int(project_id),),
    ).fetchone()
    values = {key: "" for key in literary_form.STYLE_FIELDS}
    if row is None:
        return values
    data = dict(row)
    for key in literary_form.STYLE_FIELDS:
        values[key] = str(data.get(key) or "")
    return values


def save_style_values(conn: sqlite3.Connection, project_id: int, updates: dict[str, str]) -> None:
    if not updates:
        return
    keys = [key for key in EXTRACT_FIELDS if key in updates]
    if not keys:
        return
    assignments = ", ".join(f"{key} = ?" for key in keys)
    conn.execute(
        f"UPDATE project SET {assignments} WHERE id = ?",
        (*[updates[key][: literary_form.STYLE_FIELD_MAX_CHARS] for key in keys], int(project_id)),
    )


def style_prompt(assembled: dict[str, Any], current: dict[str, str]) -> str:
    lines = ["현재 문체 칸 (구분 없이 작품 설정으로 읽는다)"]
    labels = {
        "style_narration": "서술 기본값",
        "style_sentence": "문장",
        "style_dialogue": "대화",
        "style_lexicon": "어휘와 이미지",
        "style_choice": "의도적 선택",
        "style_habit": "고치고 싶은 습관",
    }
    for key, label in labels.items():
        text = str(current.get(key) or "").strip() or "(비어 있음)"
        lines.append(f"{label}: {text}")
    template = prompt_loader.load_text("literature/style_prompt.txt")
    return prompt_loader.fill_template(
        template,
        {
            "current_style": "\n".join(lines),
            "manuscript": format_numbered_manuscript(assembled),
        },
    )


def extract_style(
    claude: Any,
    assembled: dict[str, Any],
    current: dict[str, str],
    *,
    model: str | None = None,
    cached_prefix: str = "",
) -> dict[str, Any]:
    from feedback_pipeline.literature_cache import build_cached_system, with_schema_instruction

    chosen = model or LITERATURE_STAGE_MODELS["style"]
    prompt = style_prompt(assembled, current)
    prefix = str(cached_prefix or "").strip()
    schema = prompt_loader.load_json("literature/style_schema.json")
    common = prompt_loader.load_text("literature/system.txt").rstrip()
    if prefix:
        prompt = prompt.replace(format_numbered_manuscript(assembled), "원고는 메시지 앞부분에 있다.")
        system: str | list = build_cached_system(prefix, common)
    else:
        system = common
    result = claude.generate(
        with_schema_instruction(prompt, schema),
        model=chosen,
        system=system,
        thinking="off",
        max_tokens=2000,
        timeout=120.0,
    )
    parsed = result.get("parsed") if isinstance(result, dict) else None
    if not isinstance(parsed, dict):
        parsed = {}
    return {"parsed": parsed, "result": result, "model": chosen}


def extract_and_apply(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    mode: str,
    claude: Any,
    model: str | None = None,
    cached_prefix: str = "",
    assembled: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """원고에서 1~4번을 뽑아 정책에 맞게 저장한다. 5~6번은 쓰지 않는다."""
    if mode not in {"button", "fill_empty"}:
        raise ValueError("문체 추출 방식이 올바르지 않습니다.")
    row = conn.execute(
        "SELECT cluster_id, main_genre, sub_genre, literary_form FROM project WHERE id = ?",
        (int(project_id),),
    ).fetchone()
    if literary_form.literary_track(dict(row) if row else {}) not in {"short", "long"}:
        raise ValueError("문학 작품에서만 문체를 추출해요.")
    body = assembled if isinstance(assembled, dict) else assemble_manuscript(load_scene_rows(conn, project_id))
    if not body.get("paragraphs"):
        raise ValueError("추출할 원고가 없습니다.")
    current = load_style_values(conn, project_id)
    needs_model = False
    for key in EXTRACT_FIELDS:
        state = field_state(current.get(key))
        if state == "empty" or (mode == "button" and state == "tori"):
            needs_model = True
            break
    if not needs_model:
        return {"updated": {}, "skipped": True, "usage": None, "model": model or ""}
    extracted = extract_style(claude, body, current, model=model, cached_prefix=cached_prefix)
    updates = apply_style_drafts(current, extracted["parsed"], mode=mode)
    save_style_values(conn, project_id, updates)
    return {
        "updated": updates,
        "skipped": False,
        "usage": (extracted["result"] or {}).get("usage"),
        "model": extracted["model"],
        "result": extracted.get("result"),
    }
