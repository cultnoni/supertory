"""장편 분석 단위. 실제 장이 있으면 장, 회차만 있으면 회차."""

from __future__ import annotations

from typing import Any


def literary_units(chapters: list[dict[str, Any]], scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """순서대로 분석 단위를 돌려준다.

    투명 폴더가 아닌 실제 장이 하나라도 있으면 그 장이 단위다.
    실제 장이 하나도 없을 때만 회차 하나가 단위다.
    """
    scene_rows = [row for row in scenes or [] if isinstance(row, dict)]
    chapter_rows = [row for row in chapters or [] if isinstance(row, dict)]
    by_chapter: dict[int, list[dict[str, Any]]] = {}
    for scene in scene_rows:
        by_chapter.setdefault(int(scene.get("chapter_id") or 0), []).append(scene)
    populated = [
        chapter for chapter in chapter_rows
        if by_chapter.get(int(chapter.get("id") or 0))
    ]
    populated.sort(key=lambda chapter: (int(chapter.get("sort_order") or 0), int(chapter.get("id") or 0)))
    # 회차만 있는 작품의 투명 폴더는 장으로 세지 않는다.
    real = [chapter for chapter in populated if not chapter.get("transparent")]
    if real:
        units = []
        for index, chapter in enumerate(real):
            members = sorted(
                by_chapter.get(int(chapter["id"]), []),
                key=lambda scene: (int(scene.get("sort_order") or 0), int(scene.get("id") or 0)),
            )
            units.append(
                {
                    "kind": "chapter",
                    "id": int(chapter["id"]),
                    "title": str(chapter.get("title") or ""),
                    "ord": index,
                    "scene_ids": [int(scene["id"]) for scene in members],
                }
            )
        covered = {scene_id for unit in units for scene_id in unit["scene_ids"]}
        extras = [
            scene for scene in scene_rows
            if int(scene["id"]) not in covered
        ]
        extras.sort(key=lambda scene: (int(scene.get("sort_order") or 0), int(scene.get("id") or 0)))
        for scene in extras:
            units.append(
                {
                    "kind": "scene",
                    "id": int(scene["id"]),
                    "title": str(scene.get("title") or ""),
                    "ord": len(units),
                    "scene_ids": [int(scene["id"])],
                }
            )
        return units
    loose = sorted(
        scene_rows,
        key=lambda scene: (
            int(scene.get("chapter_sort") or scene.get("sort_order") or 0),
            int(scene.get("sort_order") or 0),
            int(scene.get("id") or 0),
        ),
    )
    return [
        {
            "kind": "scene",
            "id": int(scene["id"]),
            "title": str(scene.get("title") or ""),
            "ord": index,
            "scene_ids": [int(scene["id"])],
        }
        for index, scene in enumerate(loose)
    ]


def load_literary_units(conn, project_id: int) -> list[dict[str, Any]]:
    from import_hierarchy import is_transparent_chapter

    chapters = [
        dict(row)
        for row in conn.execute(
            "SELECT id, title, notes_md, sort_order FROM chapter WHERE project_id = ? AND deleted_at IS NULL ORDER BY sort_order, id",
            (int(project_id),),
        ).fetchall()
    ]
    for chapter in chapters:
        chapter["transparent"] = is_transparent_chapter(chapter.get("notes_md"), chapter.get("title"))
    scenes = [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, s.chapter_id, s.title, s.sort_order, c.sort_order AS chapter_sort
            FROM scene AS s
            JOIN chapter AS c ON c.id = s.chapter_id
            WHERE s.project_id = ? AND s.deleted_at IS NULL AND c.deleted_at IS NULL
            ORDER BY c.sort_order, s.sort_order, s.id
            """,
            (int(project_id),),
        ).fetchall()
    ]
    return literary_units(chapters, scenes)
