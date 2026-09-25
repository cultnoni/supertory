"""일반문학 단편 원고를 회차 순서대로 이어 붙인다."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from feedback_pipeline.paragraphs import normalize_text, paragraphs_from_html, source_hash

MANUSCRIPT_MARK = "---원고---"


def loose_norm(text: str) -> str:
    """인용 비교용. 공백과 문장부호를 뺀다."""
    base = normalize_text(text)
    base = re.sub(r"\s+", "", base)
    return re.sub(r"[.,!?…·:;'\"“”‘’「」『』()\[\]{}<>\-—~]", "", base)


def quote_in_text(quote: str, source: str) -> bool:
    needle = loose_norm(quote)
    if len(needle) < 4:
        return False
    return needle in loose_norm(source)


def paper_pages(text: str) -> float:
    """원고지 매수 근사치. 공백 포함, 줄바꿈 제외, 200자."""
    chars = len(str(text or "").replace("\r", "").replace("\n", ""))
    return round(chars / 200.0, 1)


def _is_body(paragraph: dict[str, Any]) -> bool:
    if str(paragraph.get("type") or "") == "divider":
        return False
    return bool(str(paragraph.get("text") or "").strip())


def load_scene_rows(conn: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.id, s.title, s.sort_order, c.sort_order AS chapter_sort,
               r.revision_no, r.content_md
        FROM scene AS s
        JOIN chapter AS c ON c.id = s.chapter_id
        JOIN scene_revision AS r ON r.scene_id = s.id AND r.is_current = 1
        WHERE s.project_id = ?
          AND s.deleted_at IS NULL
          AND c.deleted_at IS NULL
        ORDER BY c.sort_order, c.id, s.sort_order, s.id
        """,
        (int(project_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def assemble_manuscript(scene_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """회차 스냅샷과 작품 전체 문단 번호를 만든다."""
    scenes: list[dict[str, Any]] = []
    global_rows: list[dict[str, Any]] = []
    offset = 0
    for index, row in enumerate(scene_rows):
        local = paragraphs_from_html(str(row.get("content_md") or ""))
        body = [item for item in local if _is_body(item)]
        numbered: list[dict[str, Any]] = []
        for step, item in enumerate(body, start=1):
            numbered.append(
                {
                    "n": offset + step,
                    "local": int(item.get("i") or step),
                    "scene_id": int(row["id"]),
                    "scene_title": str(row.get("title") or ""),
                    "text": str(item.get("text") or ""),
                }
            )
        scenes.append(
            {
                "scene_id": int(row["id"]),
                "title": str(row.get("title") or ""),
                "ord": index,
                "revision_no": row.get("revision_no"),
                "para_offset": offset,
                "paragraphs": local,
                "source_hash": source_hash(local),
            }
        )
        global_rows.extend(numbered)
        offset += len(body)
    plain = "\n\n".join(item["text"] for item in global_rows)
    return {
        "scenes": scenes,
        "paragraphs": global_rows,
        "plain": plain,
        "paper_pages": paper_pages(plain),
        "source_hash": source_hash(
            [{"i": item["n"], "text": item["text"], "type": "text"} for item in global_rows]
        ),
    }


def format_numbered_manuscript(assembled: dict[str, Any]) -> str:
    lines: list[str] = [MANUSCRIPT_MARK]
    current = None
    for item in assembled.get("paragraphs") or []:
        scene_id = item.get("scene_id")
        if scene_id != current:
            current = scene_id
            title = str(item.get("scene_title") or "").strip() or "회차"
            lines.append(f"# {title}")
        lines.append(f"[P{item['n']}] {item['text']}")
    if len(lines) == 1:
        lines.append("(원고 없음)")
    return "\n\n".join(lines)


def locate_quote(
    assembled: dict[str, Any],
    quote: str,
    hinted_para: int | None = None,
) -> dict[str, Any] | None:
    """인용 텍스트를 먼저 찾고, 없으면 힌트 문단 번호를 쓴다."""
    rows = list(assembled.get("paragraphs") or [])
    needle = loose_norm(quote)
    if len(needle) >= 4:
        matches = [row for row in rows if needle in loose_norm(str(row.get("text") or ""))]
        if matches:
            chosen = matches[0]
            if hinted_para:
                for row in matches:
                    if int(row.get("n") or 0) == int(hinted_para):
                        chosen = row
                        break
            return {
                "scene_id": int(chosen["scene_id"]),
                "local": int(chosen["local"]),
                "para": int(chosen["n"]),
                "quote": str(quote or "").strip(),
                "matched": "quote",
            }
    if hinted_para:
        for row in rows:
            if int(row.get("n") or 0) == int(hinted_para):
                return {
                    "scene_id": int(row["scene_id"]),
                    "local": int(row["local"]),
                    "para": int(row["n"]),
                    "quote": str(quote or "").strip(),
                    "matched": "para",
                }
    return None
