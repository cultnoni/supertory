"""장편: 이전 단위 요약·계층 압축·이웃 장면 컨텍스트."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from feedback_pipeline.config import (
    HAIKU_45_MODEL,
    LITERARY_COMPRESS_GROUP,
    LITERARY_RECENT_UNITS,
    LITERATURE_LONG_STAGE_MODELS,
)
from feedback_pipeline.literature_summary import content_hash
from feedback_pipeline.literature_text import assemble_manuscript, load_scene_rows


def unit_label(unit: dict[str, Any]) -> str:
    ord_n = int(unit.get("ord") or 0) + 1
    title = str(unit.get("title") or "").strip()
    if title:
        return f"{ord_n}장({title})"
    return f"{ord_n}장"


def find_unit_for_scene(units: list[dict[str, Any]], scene_id: int) -> dict[str, Any] | None:
    for unit in units:
        if int(scene_id) in [int(sid) for sid in (unit.get("scene_ids") or [])]:
            return unit
    return None


def assemble_unit(conn, project_id: int, scene_ids: list[int]) -> dict[str, Any]:
    """단위에 속한 회차만 이어 붙인다."""
    wanted = {int(sid) for sid in scene_ids}
    rows = [row for row in load_scene_rows(conn, project_id) if int(row["id"]) in wanted]
    rows.sort(key=lambda row: (int(row.get("chapter_sort") or 0), int(row.get("sort_order") or 0), int(row["id"])))
    return assemble_manuscript(rows)


def unit_source_hash(assembled: dict[str, Any]) -> str:
    return str(assembled.get("source_hash") or "")


def _scene_summary_text(conn, scene_id: int) -> str:
    row = conn.execute(
        "SELECT summary, stale FROM scene_summary WHERE scene_id = ?",
        (int(scene_id),),
    ).fetchone()
    if row is None:
        return ""
    raw = str(row["summary"] or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, dict):
        parts = []
        for key in ("events", "event_summary", "summary", "characters", "world_facts", "baits", "떡밥"):
            value = parsed.get(key)
            if value:
                parts.append(f"{key}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}")
        return "\n".join(parts) if parts else json.dumps(parsed, ensure_ascii=False)
    return raw


def unit_summary_from_scenes(conn, unit: dict[str, Any]) -> str:
    chunks = []
    for scene_id in unit.get("scene_ids") or []:
        text = _scene_summary_text(conn, int(scene_id))
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()


def _latest_chapter_work(conn, project_id: int, unit: dict[str, Any], current_hash: str) -> dict[str, Any] | None:
    """원고 해시가 당시 실행과 같을 때만 '이 장이 한 일'을 돌려준다."""
    rows = conn.execute(
        """
        SELECT id, report_json, params_json FROM feedback_run
        WHERE project_id = ? AND pipeline = 'literature_long' AND status IN ('ok', 'partial')
        ORDER BY id DESC
        """,
        (int(project_id),),
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
        run_hash = str(params.get("unit_source_hash") or "")
        if not run_hash:
            # 스냅샷 해시로 보완
            scene_hashes = [
                str(item["source_hash"] or "")
                for item in conn.execute(
                    "SELECT source_hash FROM feedback_run_scene WHERE run_id = ? ORDER BY ord, id",
                    (int(row["id"]),),
                ).fetchall()
            ]
            run_hash = hashlib.sha256("|".join(scene_hashes).encode("utf-8")).hexdigest()
        if run_hash != current_hash:
            return None
        try:
            report = json.loads(row["report_json"] or "{}")
        except json.JSONDecodeError:
            report = {}
        work = report.get("chapter_work")
        return work if isinstance(work, dict) else None
    return None


def neighbor_scene_texts(conn, project_id: int, units: list[dict[str, Any]], unit_ord: int) -> dict[str, str]:
    """직전 단위 마지막 장면 원문, 다음 단위 첫 장면 원문."""
    rows = {int(row["id"]): row for row in load_scene_rows(conn, project_id)}
    previous = ""
    following = ""
    if unit_ord > 0:
        prev = units[unit_ord - 1]
        last_id = int((prev.get("scene_ids") or [0])[-1])
        previous = str((rows.get(last_id) or {}).get("content_md") or "")
    if unit_ord + 1 < len(units):
        nxt = units[unit_ord + 1]
        first_id = int((nxt.get("scene_ids") or [0])[0])
        following = str((rows.get(first_id) or {}).get("content_md") or "")
    return {"previous_last_scene": previous, "next_first_scene": following}


def _bundle_hash(parts: list[str]) -> str:
    return content_hash("\n---\n".join(parts))


def load_bundle(conn, project_id: int, start_ord: int, end_ord: int, digest: str) -> str | None:
    row = conn.execute(
        "SELECT content_hash, summary_md FROM literary_summary_bundle "
        "WHERE project_id = ? AND start_ord = ? AND end_ord = ?",
        (int(project_id), int(start_ord), int(end_ord)),
    ).fetchone()
    if row is None:
        return None
    if str(row["content_hash"] or "") != digest:
        return None
    return str(row["summary_md"] or "").strip() or None


def save_bundle(conn, project_id: int, start_ord: int, end_ord: int, digest: str, summary: str) -> None:
    conn.execute(
        """
        INSERT INTO literary_summary_bundle(project_id, start_ord, end_ord, content_hash, summary_md, updated_at)
        VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        ON CONFLICT(project_id, start_ord, end_ord) DO UPDATE SET
            content_hash = excluded.content_hash,
            summary_md = excluded.summary_md,
            updated_at = excluded.updated_at
        """,
        (int(project_id), int(start_ord), int(end_ord), digest, str(summary or "")),
    )


def compress_group(
    claude: Any,
    *,
    model: str,
    label: str,
    members: list[str],
) -> dict[str, Any]:
    prompt = (
        f"다음 장 요약들을 한 덩어리로 압축한다. 사건·인물 변화·모티프만 남긴다.\n"
        f"묶음: {label}\n\n" + "\n\n".join(members)
    )
    return claude.generate(
        prompt + '\n\n출력은 {"summary":"..."} JSON만.',
        model=model,
        system="너는 장편 요약 압축기다. JSON만 출력한다.",
        thinking="off",
        max_tokens=1200,
        timeout=120.0,
    )


def build_prior_context(
    conn,
    project_id: int,
    units: list[dict[str, Any]],
    current_ord: int,
    *,
    claude: Any,
    stage_models: dict[str, str] | None = None,
    on_usage: Callable[[str, dict[str, Any], str], None] | None = None,
) -> dict[str, Any]:
    """최근 N단위는 그대로, 그 이전은 압축 묶음."""
    recent_n = LITERARY_RECENT_UNITS
    group_n = LITERARY_COMPRESS_GROUP
    custom = stage_models or {}
    compress_model = str(
        custom.get("compress")
        or LITERATURE_LONG_STAGE_MODELS.get("compress")
        or HAIKU_45_MODEL
    )
    recent_blocks: list[str] = []
    compressed_blocks: list[str] = []
    prior = units[:current_ord]
    if not prior:
        return {"text": "(이전 단위 없음)", "recent": [], "compressed": [], "usage_calls": []}

    cut = max(0, len(prior) - recent_n)
    older = prior[:cut]
    recent = prior[cut:]

    for unit in recent:
        assembled = assemble_unit(conn, project_id, list(unit.get("scene_ids") or []))
        summary = unit_summary_from_scenes(conn, unit) or ""
        from feedback_pipeline.literature_unit_work import resolve_unit_work

        resolved = resolve_unit_work(conn, project_id, unit, assembled=assembled)
        work = resolved.get("work")
        block_parts = [f"{unit_label(unit)}"]
        if work:
            block_parts.append("이 장이 한 일:\n" + json.dumps(work, ensure_ascii=False))
            if work.get("one_line"):
                block_parts.append("한 줄: " + str(work.get("one_line")))
        if summary:
            block_parts.append("회차 요약:\n" + summary)
        if len(block_parts) == 1:
            block_parts.append("(기록 없음)")
        recent_blocks.append("\n".join(block_parts))

    # 오래된 단위를 group_n개씩
    for start in range(0, len(older), group_n):
        chunk = older[start : start + group_n]
        if not chunk:
            continue
        start_ord = int(chunk[0]["ord"])
        end_ord = int(chunk[-1]["ord"])
        member_texts = []
        for unit in chunk:
            assembled = assemble_unit(conn, project_id, list(unit.get("scene_ids") or []))
            from feedback_pipeline.literature_unit_work import resolve_unit_work

            resolved = resolve_unit_work(conn, project_id, unit, assembled=assembled)
            work = resolved.get("work")
            summary = unit_summary_from_scenes(conn, unit) or ""
            if work:
                text = json.dumps(work, ensure_ascii=False)
                if work.get("one_line"):
                    text = str(work.get("one_line")) + "\n" + text
            else:
                text = summary or "(기록 없음)"
            member_texts.append(f"{unit_label(unit)}:\n{text}")
        digest = _bundle_hash(member_texts)
        cached = load_bundle(conn, project_id, start_ord, end_ord, digest)
        if cached:
            compressed_blocks.append(f"{start_ord + 1}~{end_ord + 1}장 압축:\n{cached}")
            continue
        result = compress_group(
            claude,
            model=compress_model,
            label=f"{start_ord + 1}~{end_ord + 1}장",
            members=member_texts,
        )
        if on_usage:
            on_usage(f"compress:{start_ord}-{end_ord}", result, compress_model)
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        summary = str(parsed.get("summary") or result.get("text") or "").strip()
        save_bundle(conn, project_id, start_ord, end_ord, digest, summary)
        compressed_blocks.append(f"{start_ord + 1}~{end_ord + 1}장 압축:\n{summary}")

    parts = []
    if compressed_blocks:
        parts.append("먼 과거(압축):\n" + "\n\n".join(compressed_blocks))
    if recent_blocks:
        parts.append("최근 단위:\n" + "\n\n".join(recent_blocks))
    return {
        "text": "\n\n".join(parts) if parts else "(이전 단위 없음)",
        "recent": recent_blocks,
        "compressed": compressed_blocks,
    }


def refresh_scene_summaries_for_ids(
    conn,
    project_id: int,
    scene_ids: list[int],
    summarize_scene_for_index: Callable[[int, dict], Any],
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, list[int]]:
    """없거나 낡은 회차 요약만 다시 만든다. 실패 회차는 failed에 남기고 계속한다."""
    wanted = [int(sid) for sid in scene_ids]
    if not wanted:
        if on_progress:
            on_progress(0, 0)
        return {"done": [], "failed": [], "queued": []}

    from feedback_pipeline.literature_summary import mark_summary_for_content

    rows = {
        int(row["id"]): row
        for row in conn.execute(
            """
            SELECT s.id, r.content_md
            FROM scene AS s
            JOIN scene_revision AS r ON r.scene_id = s.id AND r.is_current = 1
            WHERE s.project_id = ? AND s.deleted_at IS NULL
            """,
            (int(project_id),),
        ).fetchall()
    }
    queued: list[int] = []
    for scene_id in wanted:
        row = rows.get(int(scene_id))
        if row is None:
            continue
        state = mark_summary_for_content(conn, scene_id, str(row["content_md"] or ""))
        if state != "fresh":
            queued.append(int(scene_id))

    total = len(queued)
    if on_progress:
        on_progress(0, total)
    done_ids: list[int] = []
    failed_ids: list[int] = []
    for index, scene_id in enumerate(queued, start=1):
        row = rows[int(scene_id)]
        text = str(row["content_md"] or "")
        try:
            result = summarize_scene_for_index(scene_id, {"content_md": text})
            summary_value = result.get("summary") if isinstance(result, dict) else result
            if isinstance(summary_value, (dict, list)):
                summary = json.dumps(summary_value, ensure_ascii=False)
            else:
                summary = str(summary_value or "")
            if not str(summary or "").strip():
                raise ValueError("empty summary")
            conn.execute(
                """
                UPDATE scene_summary
                SET summary = ?, content_hash = ?, stale = 0,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE scene_id = ?
                """,
                (summary, content_hash(text), int(scene_id)),
            )
            done_ids.append(int(scene_id))
        except Exception:  # noqa: BLE001 — 요약 실패 시 해당 회차만 건너뛴다
            failed_ids.append(int(scene_id))
        if on_progress:
            on_progress(index, total)
    return {"done": done_ids, "failed": failed_ids, "queued": queued}


def refresh_prior_summaries(
    conn,
    project_id: int,
    units: list[dict[str, Any]],
    current_ord: int,
    summarize_scene_for_index: Callable[[int, dict], Any],
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[int]:
    """실행 단위보다 앞선 회차의 없거나 낡은 요약만 다시 만든다."""
    prior_scene_ids: list[int] = []
    for unit in units[:current_ord]:
        prior_scene_ids.extend(int(sid) for sid in (unit.get("scene_ids") or []))
    result = refresh_scene_summaries_for_ids(
        conn,
        project_id,
        prior_scene_ids,
        summarize_scene_for_index,
        on_progress=on_progress,
    )
    return list(result.get("done") or [])


def refresh_unit_summaries(
    conn,
    project_id: int,
    units: list[dict[str, Any]],
    summarize_scene_for_index: Callable[[int, dict], Any],
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """지정 단위(범위)에 속한 회차 요약을 준비한다. 실패 단위 번호를 함께 돌려준다."""
    scene_ids: list[int] = []
    scene_to_unit_no: dict[int, int] = {}
    for unit in units or []:
        unit_no = int(unit.get("ord") or 0) + 1
        for sid in unit.get("scene_ids") or []:
            scene_ids.append(int(sid))
            scene_to_unit_no[int(sid)] = unit_no
    result = refresh_scene_summaries_for_ids(
        conn,
        project_id,
        scene_ids,
        summarize_scene_for_index,
        on_progress=on_progress,
    )
    failed_units = sorted(
        {scene_to_unit_no[sid] for sid in (result.get("failed") or []) if sid in scene_to_unit_no}
    )
    return {
        "done": list(result.get("done") or []),
        "failed": list(result.get("failed") or []),
        "queued": list(result.get("queued") or []),
        "failed_unit_nos": failed_units,
    }
