"""가벼운 「이 장이 한 일」 캐시 (Haiku). 장편 리포트 chapter_work를 우선한다."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from feedback_pipeline import prompt_loader
from feedback_pipeline.config import HAIKU_45_MODEL
from feedback_pipeline.literature_cache import with_schema_instruction
from feedback_pipeline.literature_context import (
    _latest_chapter_work,
    assemble_unit,
    unit_label,
    unit_source_hash,
)
from feedback_pipeline.literature_text import format_numbered_manuscript

_WORK_KEYS = (
    "new_events",
    "character_changes",
    "motifs_new",
    "motifs_returned",
    "baits_resolved",
)


def normalize_unit_work(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for key in _WORK_KEYS:
        values = data.get(key) if isinstance(data.get(key), list) else []
        cleaned = []
        for item in values:
            text = " ".join(str(item or "").split()).strip()
            if text:
                cleaned.append(text[:60])
        out[key] = cleaned
    one = " ".join(str(data.get("one_line") or "").split()).strip()
    out["one_line"] = one[:80]
    return out


def work_has_substance(work: dict[str, Any] | None) -> bool:
    if not isinstance(work, dict):
        return False
    for key in _WORK_KEYS:
        if any(str(x).strip() for x in (work.get(key) or [])):
            return True
    return bool(str(work.get("one_line") or "").strip())


def load_light_unit_work(
    conn,
    project_id: int,
    unit: dict[str, Any],
    source_hash: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT work_json, source_hash, one_line FROM literary_unit_work
        WHERE project_id = ? AND unit_kind = ? AND unit_id = ?
        """,
        (int(project_id), str(unit.get("kind") or ""), int(unit.get("id") or 0)),
    ).fetchone()
    if row is None:
        return None
    if str(row["source_hash"] or "") != str(source_hash or ""):
        return None
    try:
        work = json.loads(row["work_json"] or "{}")
    except json.JSONDecodeError:
        work = {}
    if not isinstance(work, dict):
        return None
    normalized = normalize_unit_work(work)
    if not normalized.get("one_line") and row["one_line"]:
        normalized["one_line"] = str(row["one_line"] or "")[:80]
    return normalized


def save_light_unit_work(
    conn,
    project_id: int,
    unit: dict[str, Any],
    source_hash: str,
    work: dict[str, Any],
    *,
    model: str,
) -> dict[str, Any]:
    normalized = normalize_unit_work(work)
    conn.execute(
        """
        INSERT INTO literary_unit_work(
            project_id, unit_kind, unit_id, source_hash, work_json, one_line, model, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        ON CONFLICT(project_id, unit_kind, unit_id) DO UPDATE SET
            source_hash = excluded.source_hash,
            work_json = excluded.work_json,
            one_line = excluded.one_line,
            model = excluded.model,
            updated_at = excluded.updated_at
        """,
        (
            int(project_id),
            str(unit.get("kind") or ""),
            int(unit.get("id") or 0),
            str(source_hash or ""),
            json.dumps(normalized, ensure_ascii=False),
            str(normalized.get("one_line") or ""),
            str(model or ""),
        ),
    )
    return normalized


def resolve_unit_work(
    conn,
    project_id: int,
    unit: dict[str, Any],
    *,
    assembled: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """장편 리포트 chapter_work 우선, 없으면 가벼운 장 기록."""
    if assembled is None:
        assembled = assemble_unit(conn, project_id, list(unit.get("scene_ids") or []))
    digest = unit_source_hash(assembled)
    long_work = _latest_chapter_work(conn, project_id, unit, digest)
    if isinstance(long_work, dict) and work_has_substance(long_work):
        work = normalize_unit_work(long_work)
        return {"work": work, "source": "long", "source_hash": digest, "assembled": assembled}
    light = load_light_unit_work(conn, project_id, unit, digest)
    if isinstance(light, dict) and work_has_substance(light):
        return {"work": light, "source": "light", "source_hash": digest, "assembled": assembled}
    return {"work": None, "source": None, "source_hash": digest, "assembled": assembled}


def generate_light_unit_work(
    claude: Any,
    *,
    label: str,
    manuscript: str,
    model: str | None = None,
) -> dict[str, Any]:
    use_model = model or HAIKU_45_MODEL
    schema = prompt_loader.load_json("literature/unit_work_schema.json")
    prompt = prompt_loader.fill_template(
        prompt_loader.load_text("literature/unit_work_prompt.txt"),
        {
            "label": label,
            "manuscript": str(manuscript or "")[:48000],
        },
    )
    result = claude.generate(
        with_schema_instruction(prompt, schema),
        model=use_model,
        system="너는 장편 요약기다. JSON만. 판정·추측 금지.",
        thinking="off",
        max_tokens=900,
        timeout=120.0,
    )
    parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
    return {"result": result, "work": normalize_unit_work(parsed), "model": use_model}


def ensure_light_unit_works(
    conn,
    project_id: int,
    units: list[dict[str, Any]],
    claude: Any,
    *,
    model: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_usage: Callable[[str, dict[str, Any], str], None] | None = None,
) -> dict[str, Any]:
    """해시가 맞는 장편/가벼운 기록이 없는 단위만 Haiku로 만든다."""
    use_model = model or HAIKU_45_MODEL
    needing: list[dict[str, Any]] = []
    for unit in units or []:
        resolved = resolve_unit_work(conn, project_id, unit)
        if resolved.get("work"):
            continue
        needing.append({"unit": unit, "assembled": resolved["assembled"], "hash": resolved["source_hash"]})

    total = len(needing)
    if on_progress:
        on_progress(0, total)
    done: list[int] = []
    failed: list[int] = []
    created: list[dict[str, Any]] = []
    for index, item in enumerate(needing, start=1):
        unit = item["unit"]
        unit_no = int(unit.get("ord") or 0) + 1
        started = time.perf_counter()
        try:
            manuscript = format_numbered_manuscript(item["assembled"])
            if not (item["assembled"].get("paragraphs") or []):
                raise ValueError("empty manuscript")
            generated = generate_light_unit_work(
                claude,
                label=unit_label(unit),
                manuscript=manuscript,
                model=use_model,
            )
            result = generated["result"]
            if isinstance(result, dict):
                result["_elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            if on_usage:
                on_usage(f"unit_work:{unit_no}", result, use_model)
            work = save_light_unit_work(
                conn,
                project_id,
                unit,
                item["hash"],
                generated["work"],
                model=use_model,
            )
            created.append(
                {
                    "unit_no": unit_no,
                    "work": work,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                }
            )
            done.append(unit_no)
        except Exception:  # noqa: BLE001
            failed.append(unit_no)
        if on_progress:
            on_progress(index, total)
    return {
        "queued": total,
        "done": done,
        "failed": failed,
        "created": created,
        "model": use_model,
    }


__all__ = [
    "ensure_light_unit_works",
    "generate_light_unit_work",
    "load_light_unit_work",
    "normalize_unit_work",
    "resolve_unit_work",
    "save_light_unit_work",
    "work_has_substance",
]
