"""장르문학 장르 레이어 모음. 묶음 id → 모듈.

러너(runner.py)와 리포트 마크다운이 묶음을 몰라도 쓸 수 있게 한다.
각 모듈은 ``SPEC``, ``build_cards``, ``protected_paragraphs``, ``TOUCH_WARNING``,
``MD_TITLE``, ``MD_LABELS``를 가진다.
"""

from __future__ import annotations

from typing import Any

from feedback_pipeline import genre_common, genre_puzzle, genre_world

LAYERS = {
    genre_puzzle.GROUP: genre_puzzle,
    genre_world.GROUP: genre_world,
}


def layer_for(group: object) -> Any | None:
    return LAYERS.get(str(group or "").strip())


def run_report(group: object, conn, project_id, paragraphs, **kwargs) -> dict[str, Any]:
    layer = layer_for(group)
    if layer is None:
        raise ValueError("지원하지 않는 장르 묶음입니다.")
    return genre_common.run_report(layer.SPEC, conn, project_id, paragraphs, **kwargs)


def render_genre_md(genre: dict[str, Any] | None) -> str:
    """리포트 마크다운 뒤에 붙는 장르 진단 글."""
    if not isinstance(genre, dict) or not genre.get("items"):
        return ""
    layer = layer_for(genre.get("group")) or genre_puzzle
    verdict = {"works": "잘 작동함", "room": "보완 여지", "problem": "문제", "none": "해당 없음"}
    parts = [f"## {layer.MD_TITLE}"]
    summary = str(genre.get("summary") or "").strip()
    if summary:
        parts.append(summary)
    for item in genre["items"]:
        mark = " · 의도라면 유지" if item.get("intentional") else ""
        parts.append(
            f"### {layer.MD_LABELS.get(item['key'], item['key'])} — {verdict.get(item['verdict'], '')}{mark}"
        )
        if item.get("note"):
            parts.append(str(item["note"]))
        for ev in item.get("evidence") or []:
            parts.append(f"- (P{ev['para']}) {ev['quote']}")
    tasks = genre.get("top_tasks") or []
    if tasks:
        parts.append("### 우선 퇴고 과제")
        parts.extend(f"- {task['title']}: {task.get('why') or ''}" for task in tasks)
    return "\n\n".join(parts) + "\n"
