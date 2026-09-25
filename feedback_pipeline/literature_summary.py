"""장편 파이프라인용 scene_summary 캐시. 작가가 보는 요약 경로와 분리한다."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable


def content_hash(text: str) -> str:
    compact = " ".join(str(text or "").split())
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


def mark_summary_for_content(conn, scene_id: int, text: str) -> str:
    """해시가 없거나 다르면 재생성 대기열에 넣는다. 반환은 missing, stale, fresh."""
    digest = content_hash(text)
    row = conn.execute(
        "SELECT content_hash, summary FROM scene_summary WHERE scene_id = ?",
        (int(scene_id),),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO scene_summary(scene_id, summary, content_hash, stale) VALUES (?, '', ?, 1)",
            (int(scene_id), digest),
        )
        return "missing"
    if str(row["content_hash"] or "") != digest or not str(row["summary"] or "").strip():
        conn.execute(
            "UPDATE scene_summary SET content_hash = ?, stale = 1 WHERE scene_id = ?",
            (digest, int(scene_id)),
        )
        return "stale" if str(row["summary"] or "").strip() else "missing"
    return "fresh"


def queue_project_summaries(conn, project_id: int) -> list[int]:
    """없거나 낡은 회차 요약을 대기열에 넣고 id 목록을 돌려준다."""
    queued: list[int] = []
    rows = conn.execute(
        """
        SELECT s.id, r.content_md
        FROM scene AS s
        JOIN scene_revision AS r ON r.scene_id = s.id AND r.is_current = 1
        WHERE s.project_id = ? AND s.deleted_at IS NULL
        ORDER BY s.sort_order, s.id
        """,
        (int(project_id),),
    ).fetchall()
    for row in rows:
        state = mark_summary_for_content(conn, int(row["id"]), str(row["content_md"] or ""))
        if state != "fresh":
            queued.append(int(row["id"]))
    return queued


def refresh_queued_summaries(
    conn,
    project_id: int,
    summarize_scene_for_index: Callable[[int, dict], Any],
) -> list[int]:
    """대기 중인 요약을 summarize_scene_for_index(scene_id, body)로 다시 쓴다.

    작가가 보는 회차 요약 API는 부르지 않는다. 테스트는 가짜 함수를 넘긴다.
    """
    rows = conn.execute(
        """
        SELECT s.id, r.content_md
        FROM scene AS s
        JOIN scene_revision AS r ON r.scene_id = s.id AND r.is_current = 1
        JOIN scene_summary AS ss ON ss.scene_id = s.id
        WHERE s.project_id = ? AND s.deleted_at IS NULL AND ss.stale = 1
        ORDER BY s.id
        """,
        (int(project_id),),
    ).fetchall()
    done: list[int] = []
    for row in rows:
        scene_id = int(row["id"])
        text = str(row["content_md"] or "")
        result = summarize_scene_for_index(scene_id, {"content_md": text})
        summary_value = result.get("summary") if isinstance(result, dict) else result
        if isinstance(summary_value, (dict, list)):
            summary = json.dumps(summary_value, ensure_ascii=False)
        else:
            summary = str(summary_value or "")
        conn.execute(
            """
            UPDATE scene_summary
            SET summary = ?, content_hash = ?, stale = 0,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE scene_id = ?
            """,
            (summary, content_hash(text), scene_id),
        )
        done.append(scene_id)
    return done


def note_scene_content_for_long_form(conn, scene_id: int, text: str) -> str | None:
    """문학 장편일 때만 해시를 비교한다. 다른 트랙은 아무것도 하지 않는다."""
    row = conn.execute(
        """
        SELECT p.cluster_id, p.main_genre, p.sub_genre, p.literary_form
        FROM project AS p
        JOIN scene AS s ON s.project_id = p.id
        WHERE s.id = ?
        """,
        (int(scene_id),),
    ).fetchone()
    if row is None:
        return None
    from literary_form import literary_track

    if literary_track(dict(row)) != "long":
        return None
    return mark_summary_for_content(conn, scene_id, text)
