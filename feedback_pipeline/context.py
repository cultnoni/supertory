"""프로젝트 정보 문자열과 설명 렌즈. app.py를 가져오지 않는다."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

# 문학 계열 → strong. 웹소설·장르(로맨스·판타지 등)와 그 외 → normal.
# DB 장르 값: main_genre는 클러스터(웹소설/장르문학/문학/동화),
# sub_genre는 genre_clusters.json의 하위 장르.
STRONG_GENRE_MARKERS = ("에세이", "순문학", "서정", "일반문학", "문학")
NORMAL_GENRE_MARKERS = (
    "로맨스",
    "판타지",
    "웹소설",
    "무협",
    "로맨스 판타지",
    "여성향 판타지",
    "장르문학",
)


def _load_json(raw: Any, fallback: Any) -> Any:
    if raw is None:
        return fallback
    if isinstance(raw, (list, dict)):
        return raw
    text = str(raw).strip()
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


def _truncate(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 1)].rstrip() + "…"


def explanation_lens_for_project(
    genre_main: str | None, genre_sub: str | None, genre_detail: str | None = None
) -> str:
    """에세이·순문학·서정 계열은 strong, 로맨스·판타지·웹소설과 그 외는 normal."""
    blob = " ".join(
        str(part or "").strip() for part in (genre_main, genre_sub, genre_detail) if part
    )
    if any(marker in blob for marker in STRONG_GENRE_MARKERS):
        # '장르문학'에 '문학'이 들어 있으므로 웹소설/장르 마커가 더 구체적이면 normal.
        if any(marker in blob for marker in ("웹소설", "로맨스", "판타지", "무협", "장르문학")):
            if any(marker in blob for marker in ("에세이", "순문학", "서정", "일반문학")):
                return "strong"
            return "normal"
        return "strong"
    return "normal"


def build_project_context(conn: sqlite3.Connection, project_id: int) -> str:
    """제목·장르·인물·인덱스·키워드·설정집 요약을 줄 단위로 만든다.

    SuperToryHandler._tory_active_project_context는 이미 조립된 라벨을 받아
    프롬프트 블록을 만든다. app.py를 가져오면 순환 import가 나므로 여기선
    SQL로 읽고 같은 항목을 줄 단위로 쓴다.
    """
    project_id = int(project_id)
    cols = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(project)").fetchall()
    }
    select = ["id", "title"]
    for name in (
        "main_genre",
        "sub_genre",
        "genre_detail",
        "purpose",
        "keywords",
        "worldbuilding_md",
        "description_md",
    ):
        if name in cols:
            select.append(name)
    row = conn.execute(
        f"SELECT {', '.join(select)} FROM project WHERE id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        return "제목: (없음)\n장르: (없음)"
    data = dict(row)
    lines: list[str] = []
    title = str(data.get("title") or "").strip() or "(없음)"
    lines.append(f"제목: {title}")
    main = str(data.get("main_genre") or "").strip()
    sub = str(data.get("sub_genre") or "").strip()
    detail = str(data.get("genre_detail") or "").strip()
    genre_parts = [p for p in (main, sub, detail) if p]
    lines.append("장르: " + (" / ".join(genre_parts) if genre_parts else "(없음)"))

    characters = conn.execute(
        "SELECT id, name, short_description FROM character "
        "WHERE project_id = ? AND deleted_at IS NULL ORDER BY sort_order, id",
        (project_id,),
    ).fetchall()
    if characters:
        lines.append("인물:")
        for person in characters:
            name = str(person["name"] or "").strip()
            note = str(person["short_description"] or "").strip()
            aliases = [
                str(alias["alias"] or "").strip()
                for alias in conn.execute(
                    "SELECT alias FROM character_alias WHERE character_id = ? ORDER BY alias",
                    (int(person["id"]),),
                ).fetchall()
            ]
            aliases = [a for a in aliases if a and a != name]
            extra = []
            if aliases:
                extra.append("별칭 " + ", ".join(aliases))
            if note:
                extra.append(note)
            if extra:
                lines.append(f"- {name}: {'; '.join(extra)}")
            elif name:
                lines.append(f"- {name}")

    index_row = None
    try:
        index_row = conn.execute(
            "SELECT tracked_facts_json, timeline_json, open_threads_json "
            "FROM project_index WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    except sqlite3.Error:
        index_row = None
    if index_row is not None:
        facts = _load_json(index_row["tracked_facts_json"], [])
        if isinstance(facts, list) and facts:
            lines.append("확정 사실:")
            for fact in facts[:20]:
                if isinstance(fact, dict):
                    text = str(fact.get("text") or fact.get("fact") or "").strip()
                else:
                    text = str(fact or "").strip()
                if text:
                    lines.append(f"- {text}")
        timeline = _load_json(index_row["timeline_json"], [])
        if isinstance(timeline, list) and timeline:
            lines.append("시간선:")
            for item in timeline[:12]:
                if isinstance(item, dict):
                    text = str(item.get("text") or item.get("event") or "").strip()
                else:
                    text = str(item or "").strip()
                if text:
                    lines.append(f"- {_truncate(text, 120)}")
        threads = _load_json(index_row["open_threads_json"], [])
        if isinstance(threads, list) and threads:
            lines.append("열린 실마리:")
            for item in threads[:12]:
                if isinstance(item, dict):
                    if item.get("resolved"):
                        continue
                    text = str(item.get("text") or "").strip()
                else:
                    text = str(item or "").strip()
                if text:
                    lines.append(f"- {_truncate(text, 120)}")

    keywords = _load_json(data.get("keywords"), [])
    if isinstance(keywords, list) and keywords:
        labels = [str(k).strip() for k in keywords if str(k).strip()]
        if labels:
            lines.append("세계관 키워드: " + ", ".join(labels[:20]))

    world = str(data.get("worldbuilding_md") or data.get("description_md") or "").strip()
    if world:
        lines.append("설정집 요약: " + _truncate(world, 600))
    return "\n".join(lines)
