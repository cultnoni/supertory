"""측정 스크립트용 단계 재사용. 앱 화면 실행은 이 모듈을 부르지 않는다."""

from __future__ import annotations

import json
from typing import Any

STAGE_ORDER = ("style", "scene_map", "rubric", "report", "cards")


def manuscript_hashes(assembled: dict[str, Any]) -> list[str]:
    return [str(scene.get("source_hash") or "") for scene in assembled.get("scenes") or []]


def stored_scene_hashes(conn, run_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT source_hash FROM feedback_run_scene WHERE run_id = ? ORDER BY ord, id",
        (int(run_id),),
    ).fetchall()
    return [str(row["source_hash"] or "") for row in rows]


def load_reuse(conn, run_id: int, assembled: dict[str, Any], start: str) -> tuple[dict[str, Any] | None, str]:
    """해시가 같으면 재사용 옵션을 돌려준다. 다르면 None과 경고 문장.

    start가 cards면 문체·장면 지도·루브릭·리포트를 재사용하고 카드만 다시 돌린다.
    """
    stage = str(start or "cards").strip()
    if stage not in STAGE_ORDER:
        return None, f"알 수 없는 단계입니다: {stage}"
    current = manuscript_hashes(assembled)
    previous = stored_scene_hashes(conn, run_id)
    if current != previous:
        return None, (
            f"원고가 이전 실행 {int(run_id)}과 달라 재사용하지 않습니다. "
            f"문단 해시 {len(previous)}개와 현재 {len(current)}개가 다릅니다."
        )
    row = conn.execute(
        "SELECT report_json FROM feedback_run WHERE id = ?",
        (int(run_id),),
    ).fetchone()
    report: dict[str, Any] = {}
    if row is not None and row["report_json"]:
        try:
            parsed = json.loads(row["report_json"])
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            report = parsed
    if stage in {"report", "cards"} and not report:
        return None, f"이전 실행 {int(run_id)}에 재사용할 리포트가 없습니다."
    if stage == "cards" and not (report.get("scene_map") and report.get("rubric") is not None):
        return None, (
            f"이전 실행 {int(run_id)}에 장면 지도나 루브릭이 없어 "
            "카드부터 재사용할 수 없습니다."
        )
    return {
        "from": stage,
        "source_run_id": int(run_id),
        "scene_map": report.get("scene_map") or {},
        "chapter_work": report.get("chapter_work") or {},
        "rubric": report.get("rubric") or {},
        "report": report,
    }, ""
